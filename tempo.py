import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from tkinter import scrolledtext
from tkinter import messagebox
from datetime import datetime
import configparser
import os
import subprocess
import threading
import queue
import time
import logging
import re

# Default values for the application
DFLT_FFMPEG_PATH = "d:/PF/_Tools/ffmpeg/bin/ffmpeg.exe"  # Change this if your ffmpeg path is different.
DFLT_TEMPO = 1.8
DFLT_N_THREADS = 4
DFLT_CONFIG_FILE = "tempo_config.ini"
GUI_TIMEOUT = 0.1
DFLT_BITRATE_KB = 64 # i.e. 64K


#############################################################################
class CustomProgressBar(tk.Canvas):
  """
  Custom progress bar class for displaying processing progress.
  Inherits from tkinter Canvas widget.
  """
  def __init__(self, master, *args, **kwargs):
    super().__init__(master, *args, **kwargs)
    self.progress_var = tk.DoubleVar()
    self.filename_var = tk.StringVar()
    self.progress_rect = None
    self.text_id = None
    self.outline_rect = None
    self.draw_progress_bar()


  #############################################################################
  def draw_progress_bar(self):
    """Redraws the progress bar based on current progress and filename."""
    self.delete("all")
    width = self.winfo_width()
    height = self.winfo_height()
    progress = self.progress_var.get()
    fill_width = (width - 4) * (progress / 100)
    self.create_rectangle(2, 2, width - 2, height - 2, outline="black")  # Outline
    self.create_rectangle(2, 2, fill_width + 2, height - 2, fill="#A8D8A8")  # Filled progress
    self.create_text(width / 2, height / 2, text=self.filename_var.get(), fill="black")  # Filename


  #############################################################################
  def set_progress(self, value):
    """Sets the progress value and redraws the bar."""
    self.progress_var.set(value)
    self.draw_progress_bar()


  #############################################################################
  def set_display_text(self, display_text):
    """Sets the display text (filename) and redraws the bar."""
    self.filename_var.set(display_text)
    self.draw_progress_bar()


#############################################################################
class MP3Processor:
  """
  Main class for the MP3 tempo changer application.
  Handles GUI interaction, configuration, and processing logic.
  """
  def __init__(self, master):
    self.master = master
    master.title("MP3 Tempo Changer")

    # Default values (used only if config file loading fails)
    self.ffmpeg_path_default = DFLT_FFMPEG_PATH
    self.tempo_default = DFLT_TEMPO
    self.src_dir_default = ""
    self.dst_dir_default = ""
    self.n_threads_default = DFLT_N_THREADS
    self.overwrite_options = tk.StringVar(value="Skip existing files")
    self.use_compression_var = tk.BooleanVar(value=False)

    # Pre-define GUI element variables (to avoid linter warnings)
    self.run_button = None

    # Load application configuration
    self.config = configparser.ConfigParser()
    self.load_config()

    # Initialize GUI variables as empty
    self.ffmpeg_path = tk.StringVar()
    self.tempo = tk.DoubleVar()
    self.src_dir = tk.StringVar()
    self.dst_dir = tk.StringVar()
    config_n_threads = self.config.getint('DEFAULT', 'n_threads', fallback=self.n_threads_default)
    self.n_threads = tk.IntVar(value=max(1, min(16, config_n_threads)))  # Ensures value is between 1 and 16

    # Set the values using the loaded configuration or defaults
    self.ffmpeg_path.set(self.config['DEFAULT'].get('ffmpeg_path', self.ffmpeg_path_default))
    self.tempo.set(float(self.config['DEFAULT'].get('tempo', str(self.tempo_default))))
    self.src_dir.set(self.config['DEFAULT'].get('src_dir', self.src_dir_default))
    self.dst_dir.set(self.config['DEFAULT'].get('dst_dir', self.dst_dir_default))
    self.n_threads.set(int(self.config['DEFAULT'].get('n_threads', str(self.n_threads_default))))
    self.overwrite_options.set(self.config['DEFAULT'].get('overwrite_option', "Skip existing files"))  # Load overwrite option

    self.progress_bars = []
    self.active_threads = 0
    self.total_files = 0
    self.processed_files = 0
    self.processed_files_lock = threading.Lock()  # Lock for thread-safe access
    self.processed_sz_arr = {}
    self.processed_sz_arr_lock = threading.Lock()  # Lock for thread-safe access
    self.total_dst_sz_kb = 0  # Total size of all files
    self.total_dst_sz_lock = threading.Lock()  # Lock for thread-safe access
    self.total_src_sz = 0
    self.error_files = 0
    self.skipped_files = 0
    self.status_text = None
    self.start_time = None
    self.use_compression = False
    self.processing_complete = False
    self.processed_files_set = set()
    self.processing_complete_event = threading.Event()

    # Create GUI elements
    self.create_widgets()
    # Initialize threading components
    self.queue = queue.Queue()
    self.gui_queue = queue.Queue()  # Queue for GUI updates
    self.threads = []

    # Bind the save_config method to the window close event.
    self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

    self.setup_logging('DEBUG')  # 'INFO' or 'DEBUG' for more detailed logging
    logging.info("MP3Processor initialized")

    self.status_update_queue = queue.Queue()
    self.status_update_thread = threading.Thread(target=self.process_status_updates, daemon=True) # Explicitly set daemon
    self.status_update_thread.start()
    logging.info("Status update thread started.")


  #############################################################################
  def load_config(self):
    """Loads config from tempo_config.ini or uses defaults if not found."""
    if not self.config.read(DFLT_CONFIG_FILE):
      logging.warning("Config file not found. Using defaults.")
      self.config['DEFAULT'] = {
        'ffmpeg_path': DFLT_FFMPEG_PATH,
        'tempo': str(DFLT_TEMPO),
        'src_dir': '',
        'dst_dir': '',
        'n_threads': str(DFLT_N_THREADS),
        'overwrite_option': 'Skip existing files',  # Skip by default
        'use_compression': 'false',  # No compression by default
      }
    else:
      try:
        self.config['DEFAULT']['tempo'] = self.config['DEFAULT']['tempo'].split(';')[0].strip()
        # Load overwrite setting
        self.overwrite_options.set(self.config['DEFAULT'].get('overwrite_option', 'Skip existing files'))
        # Load compression setting
        self.use_compression_var.set(self.config['DEFAULT'].get('use_compression', 'false').lower() == 'true')
      except (KeyError, IndexError, ValueError):
        messagebox.showwarning("Config Error", "Tempo value missing or malformed. Using default.")
        self.config['DEFAULT']['tempo'] = str(DFLT_TEMPO)


  #############################################################################
  def save_config(self):
    """Saves application configuration to tempo_config.ini."""
    if self.validate_tempo():
      self.config['DEFAULT']['tempo'] = str(self.tempo.get())
    else:
      self.config['DEFAULT']['tempo'] = str(self.tempo_default)

    self.config['DEFAULT']['n_threads'] = str(self.n_threads.get())
    self.config['DEFAULT']['ffmpeg_path'] = self.ffmpeg_path.get()
    self.config['DEFAULT']['src_dir'] = self.src_dir.get()
    self.config['DEFAULT']['dst_dir'] = self.dst_dir.get()
    self.config['DEFAULT']['overwrite_option'] = self.overwrite_options.get()
    self.config['DEFAULT']['use_compression'] = str(self.use_compression_var.get()).lower()
    try:
      with open(DFLT_CONFIG_FILE, 'w') as configfile:
        self.config.write(configfile)
    except Exception as e:
      messagebox.showerror("Error", f"Could not save config file: {e}")


  #############################################################################
  def create_widgets(self):
    """Creates and arranges GUI elements."""
    # Tempo
    ttk.Label(self.master, text="Tempo:").grid(row=0, column=0, sticky=tk.W, padx=5)
    ttk.Entry(self.master, textvariable=self.tempo, width=5).grid(row=0, column=1, sticky=tk.W)
    ttk.Entry(self.master, textvariable=self.tempo, width=5).bind('<FocusOut>', self.on_tempo_focusout)

    # Source Directory Path
    ttk.Button(self.master, text="SrcDir", command=self.browse_src_dir).grid(row=1, column=0)
    ttk.Entry(self.master, textvariable=self.src_dir, width=200).grid(row=1, column=1, sticky=tk.W)

    # Destination Directory Path
    ttk.Button(self.master, text="DstDir", command=self.browse_dst_dir).grid(row=2, column=0)
    ttk.Entry(self.master, textvariable=self.dst_dir, width=200).grid(row=2, column=1, sticky=tk.W)

    # Number of threads 1-16
    ttk.Label(self.master, text="Number of threads:").grid(row=3, column=0, sticky=tk.W, padx=5)
    n_thread_values = list(range(1, 17))  # Creates a list from 1 to 16
    self.n_threads_combo = ttk.Combobox(self.master, textvariable=self.n_threads, values=n_thread_values, width=3, state="readonly")
    self.n_threads_combo.grid(row=3, column=1, sticky=tk.W)

    # Overwrite choice
    ttk.Label(self.master, text="File Overwrite Options:").grid(row=4, column=0, sticky=tk.W, padx=5)
    self.overwrite_options_combobox = ttk.Combobox(self.master,
      textvariable=self.overwrite_options,
      values=[ "Skip existing files", "Overwrite existing files", "Rename existing files"],
      state="readonly")
    self.overwrite_options_combobox.grid(row=4, column=1, sticky=tk.W)

    # Compression Checkbox
    ttk.Checkbutton(self.master, text="Use compression", variable=self.use_compression_var).grid(row=5, column=0, sticky=tk.W, padx=5)

    # Run button
    self.run_button = tk.Button(self.master, text="Run", command=self.start_processing, state=tk.NORMAL, height=2, width=20)
    self.run_button.grid(row=6, column=1, pady=10)  # Added pady for vertical space

    # Create a frame to hold the status_text and scrollbar
    status_frame = ttk.Frame(self.master)
    status_frame.grid(row=7, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)

    # Create the status_text widget
    self.status_text = tk.Text(status_frame, height=10, width=165, wrap=tk.WORD, state=tk.DISABLED)
    self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Create the scrollbar
    scrollbar = ttk.Scrollbar(status_frame, orient=tk.VERTICAL, command=self.status_text.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Configure the status_text to use the scrollbar
    self.status_text.config(yscrollcommand=scrollbar.set)

    # Create overall (total) progress bar
    ttk.Label(self.master, text="Overall progress:").grid(row=8, column=0, sticky=tk.W, padx=5)
    self.total_progress = CustomProgressBar(self.master, width=1202, height=25)
    self.total_progress.grid(row=8, column=1, pady=10)  # Place it above progress bars for processed files


  #############################################################################
  def browse_src_dir(self):
    """Opens a directory selection dialog for the source directory."""
    directory = filedialog.askdirectory(initialdir=self.src_dir.get())
    if directory:  # Check if a directory was selected
      self.src_dir.set(os.path.normpath(directory))


  #############################################################################
  def browse_dst_dir(self):
    """Opens a directory selection dialog for the destination directory."""
    directory = filedialog.askdirectory(initialdir=self.dst_dir.get())
    if directory:  # Check if a directory was selected
      self.dst_dir.set(os.path.normpath(directory))


  #############################################################################
  def get_mp3_info(self, ffmpeg_path, src_file_path):
    """Gets MP3 info (Duration, Bitrate, processed size) using ffmpeg."""
    try:
      command = [ffmpeg_path, "-i", src_file_path, "-hide_banner"]
      logging.debug(f"GetMp3InfoFFMPEG command: {' '.join(command)}")
      process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      stdout, stderr = process.communicate()

      # Decode stderr to a string
      try:
        ffmpeg_out = stderr.decode('utf-8', errors='replace')
      except UnicodeDecodeError:
        ffmpeg_out = stderr.decode('cp1251', errors='replace')
      except Exception as e:
        logging.error(f"Error decoding GetMp3InfoFFMPEG command: {e}")
        raise e
      # end try

      #  Example: "Duration: 00:01:25.49, start: 0.000000, bitrate: 69 kb/s"
      match = re.search(r"Duration: \d+:(\d+):(\d+)\.\d+, start: \d+\.\d+, bitrate: (\d+) kb\/s", ffmpeg_out)
      if match:
        minutes, seconds, bitrate_kbps = match.groups()
        total_seconds = int(minutes) * 60 + int(seconds)
        return int(bitrate_kbps), total_seconds, True
      else:
        logging.error(f"Error parsing ffmpeg output: {ffmpeg_out}")
        return None, None, False

    except Exception as e:
      logging.exception(f"Error getting MP3 info for {src_file_path}: {e}")
      return None, None, False


  #############################################################################
  def handle_overwrite(self, dst_file_path, relative_path):
    """Handles overwrite logic based on user selection."""
    msg = ""
    overwrite_option = self.overwrite_options.get()
    if os.path.exists(dst_file_path):
      if overwrite_option == "Overwrite existing files":  # Overwrite existing
        msg = f"Overwriting: {relative_path}"
        self.status_update_queue.put(msg)  # Use queue for status updates
        logging.debug(msg)
        return dst_file_path
      elif overwrite_option == "Rename existing files":  # Rename instead of overwriting
        base, ext = os.path.splitext(relative_path)
        i = 1
        while os.path.exists(os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")):
          i += 1
        dst_file_path = os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")
        msg = f"Renaming: {relative_path} to {os.path.basename(dst_file_path)}"
        self.status_update_queue.put(msg)  # Use queue for status updates
        logging.debug(msg)
        return dst_file_path
      elif overwrite_option == "Skip existing files":  # Skip processing
        msg = f"Skipping: {relative_path}"
        self.status_update_queue.put(msg)  # Use queue for status updates
        logging.debug(msg)
        return None  # Skip processing this file
    else:  # Normal output (no overwrite)
      msg = f"Processing: {relative_path}"
      self.status_update_queue.put(msg)  # Use queue for status updates
      logging.debug(msg)
      return dst_file_path


  #############################################################################
  def generate_ffmpeg_command(self, src_file_path, dst_file_path, bit_rate):
    """Generates the FFMPEG command with tempo and optional compression."""
    # Convert paths to string and handle potential encoding issues
    src_file_path = str(src_file_path)
    dst_file_path = str(dst_file_path)

    ffmpeg_command = [
      str(self.ffmpeg_path.get()),
      "-i", src_file_path,
      "-filter:a", f"atempo={self.tempo.get()}",  # audio filter
      "-vn",  # Disable video stream
#      "-b:a", f"{bit_rate}k",  # Fixed Bit-Rate
      dst_file_path,
      "-y",  # Force overwrite output file
      # To minimize FFmpeg’s output and only show the line with progress updates
      "-hide_banner",
      "-loglevel", "error",
      "-stats",
    ]

    if self.use_compression_var.get():
      ffmpeg_compression_params = [
        "-codec:a", "libmp3lame",  # LAME (Lame Ain't an MP3 Encoder) MP3 encoder wrapper
        "-q:a", "7",  # quality setting for VBR
        "-ar", "22050"  # sample rate
      ]
      # Insert after "src_file_path" before "-filter:a"
      ffmpeg_command[3:3] = ffmpeg_compression_params

    logging.debug(f"ProcessFile: FFMPEG command: {' '.join(ffmpeg_command)}")
    return ffmpeg_command


  #############################################################################
  def monitor_progress(self, process, progress_bar, dst_est_sz_kbt, relative_path):
    """Monitors FFMPEG progress for each mp3 file and updates the progress bar."""
    q = queue.Queue()

    def read_stderr(p, q):
      try:
        # Open stderr in binary mode
        while True:
          line = p.stderr.readline()
          if not line:
            break
          try:
            # Always treat as bytes and decode
            if isinstance(line, bytes):
              try:
                line = line.decode('utf-8', errors='replace')
              except UnicodeDecodeError:
                line = line.decode('cp1251', errors='replace')
            q.put(line)
          except Exception as e:
            logging.error(f"Error decoding line: {e}")
            continue
      except Exception as e:
        logging.error(f"Error reading stderr: {e}")
      finally:
        q.put(None)

    # Create process with binary output
    stderr_thread = threading.Thread(target=read_stderr, args=(process, q))
    stderr_thread.daemon = True
    stderr_thread.start()

    processed_sz_kb = 0
    try:
      while True:
        try:
          line = q.get(timeout=GUI_TIMEOUT)
          if line is None:
            break
          match = re.search(r"size=\s*(\d+)\w+", line)
          if match:
            processed_sz_kb = int(match.group(1))  # in KB
            progress = min(100, (processed_sz_kb / dst_est_sz_kbt) * 100) # Do not exceed 100%
            progress_bar.set_progress(progress)
            self.master.update_idletasks()

            with self.processed_sz_arr_lock:
              self.processed_sz_arr[relative_path] = processed_sz_kb #Store by filename
            self.update_total_progress()

        except queue.Empty:
          if process.poll() is not None:
            break
          time.sleep(GUI_TIMEOUT)

    except Exception as e:
      logging.exception(f"Error monitoring progress for {relative_path}: {e}")
    finally:
      progress_bar.set_progress(100)
      with self.processed_files_lock:
        self.processed_files += 1
      self.master.update_idletasks()
      stderr_thread.join()
    return processed_sz_kb


  #############################################################################
  def update_total_progress(self):
    """Updates the total progress bar based on cumulative processed size."""
    with self.processed_sz_arr_lock:
      total_processed_size_kb = sum(self.processed_sz_arr.values())
    total_progress_percentage = int((total_processed_size_kb / self.total_dst_sz_kb) * 100) if self.total_dst_sz_kb > 0 else 0
    # We're using DFLT_BITRATE_KB=64K as upper limit, but it can make the dst_est_sz_kbt < total_processed_size_kb
    # Leading to progress bars >100%. Thus will manually limit any values >=100% to 100%
    total_progress_percentage = min(100, total_progress_percentage)
    logging.debug(f"ttl_prcssd_sz_kb={total_processed_size_kb}, ttl_sz={self.total_dst_sz_kb}, prgrss={total_progress_percentage}")
    total_progress_message = f"{self.processed_files}/{self.total_files} {total_progress_percentage}%"
    self.total_progress.set_progress(total_progress_percentage)
    self.total_progress.set_display_text(total_progress_message)

    # When all files processed, set progress to 100% (might be a bit smaller/larger otherwise)
    if self.processed_files == self.total_files:
      total_progress_percentage = 100
      total_progress_message = f"{self.processed_files}/{self.total_files} {total_progress_percentage}%"
      self.total_progress.set_progress(total_progress_percentage)
      self.total_progress.set_display_text(total_progress_message)
      self.master.after(100, self.finish_processing)


  #############################################################################
  def process_file(self, src_file_path, relative_path, progress_bar):
    """Processes a single MP3 file, handling potential overwrites."""
    if relative_path in self.processed_files_set:
      return  # Skip if already processed

    self.processed_files_set.add(relative_path)
    try:
      dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
      dst_file_path = self.handle_overwrite(dst_file_path, relative_path)
      if dst_file_path is None:
        return  # Do not process, if the file should be skipped

      os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)

      # Get pre-calculated file info
      file_data = self.file_info[relative_path]
      dst_bitrate = file_data["dst_bitrate"]
      duration = file_data["duration"]
      dst_est_sz_kbt = file_data["dst_est_sz_kbt"]

      # Display processed filename in progress bar
      progress_bar.set_display_text(os.path.basename(dst_file_path))

      # Generate ffmpeg command with tempo and optional compression
      ffmpeg_command = self.generate_ffmpeg_command(src_file_path, dst_file_path, dst_bitrate)
      # Start FFMPEG process in binary mode for each file (n_threads)
      process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # Use binary mode
        bufsize=1    # Line buffered
      )
      # Monitor and update each mp3 file processing progress
      self.monitor_progress(process, progress_bar, dst_est_sz_kbt, relative_path)
      self.master.update_idletasks()

    except Exception as e:
      msg = f"Error processing {relative_path}: {e}"
      logging.exception(msg)
      self.status_update_queue.put(msg)
      self.error_files += 1
    finally:
      self.update_total_progress() # Update total progress after each file


  #############################################################################
  def queue_mp3_files(self):
    """Find, count, queue MP3 files, and pre-calculate output sizes."""
    src_dir = self.src_dir.get()
    self.total_files = 0
    self.queue = queue.Queue()
    self.file_info = {}  # Dictionary to store file info
    self.processed_files_set.clear()
    self.processed_sz_arr.clear()
    self.total_dst_sz_kb = 0
    self.total_src_sz = 0

    for root, _, files in os.walk(src_dir):
      for file in files:
        if file.lower().endswith(".mp3"):
          full_path = os.path.join(root, file)
          relative_path = os.path.relpath(full_path, src_dir)
          self.queue.put((full_path, relative_path))
          self.total_files += 1
          self.total_src_sz += os.path.getsize(full_path)

          # Get MP3 info and calculate size only if not skipping
          overwrite_option = self.overwrite_options.get()
          dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
          if os.path.exists(dst_file_path) and overwrite_option == "Skip existing files":
            self.skipped_files += 1
            self.file_info[relative_path] = {"dst_bitrate": 0, "duration": 0, "dst_est_sz_kbt": 0}
          else:
            src_bitrate, duration, success = self.get_mp3_info(self.ffmpeg_path.get(), full_path)
            if success:
              # In FFMPEG fixed bitrate (-b:a 64k) doesn't work in combination wtih Quality Setting for VBR (-q:a 7)
              # According to GPT for VBR (-q:a 7) has ~100K average bitrate. In practise it is closer to 50-65K.
              # We'll use DFLT_BITRATE_KB=64K as upper limit, but it can make the dst_est_sz_kbt < total_processed_size_kb
              # Leading to progress bars >100%. Thus will manually limit any values >=100% to 100%
              dst_bitrate = min(DFLT_BITRATE_KB, src_bitrate)
              dst_est_sz_kbt = int(dst_bitrate * duration / (8 * self.tempo.get())) # in KB
              self.file_info[relative_path] = {"dst_bitrate": dst_bitrate, "duration": duration, "dst_est_sz_kbt": dst_est_sz_kbt}
              self.total_dst_sz_kb += dst_est_sz_kbt
            else:
              logging.error(f"Could not get MP3 info for {full_path}")
              self.error_files += 1

    msg = f"{self.total_files} files found, {self.total_src_sz/(1024*1024):.2f} MB"
    self.update_status(msg)
    logging.info(msg)


  #############################################################################
  def start_process_files_threads(self):
    """Starts worker threads to process the files."""
    num_threads = min(self.n_threads.get(), self.total_files)
    self.active_threads = num_threads
    for i in range(num_threads):
      thread = threading.Thread(target=self.worker, args=(i,))
      self.threads.append(thread)
      thread.start()


  #############################################################################
  def worker(self, thread_index):
    """Worker function for each thread, processing files from the queue."""
    while True:
      try:
        file_path, relative_path = self.queue.get(timeout=1)
        self.process_file(file_path, relative_path, self.progress_bars[thread_index])
        self.queue.task_done()
      except queue.Empty:
        break
      except Exception as e:
        msg = f"Error in thread {thread_index + 1}: {e}"
        self.status_update_queue.put(msg)
        logging.exception(msg)
        break
    self.active_threads -= 1
    if self.active_threads == 0:
      self.master.after(100, self.finish_processing)


  #############################################################################
  def on_closing(self):
    """Handles window closing event, saving configuration."""
    logging.info("Closing application, waiting for threads...")
    self.save_config()

    # Attempt to gracefully stop the status update thread
    self.status_update_queue.put(None) #Signal to the thread to exit

    try:
      self.status_update_thread.join(timeout=2)
    except Exception as e:
      logging.error(f"Error joining status update thread: {e}")

    self.master.destroy()


  #############################################################################
  def start_processing(self):
    """Starts the MP3 processing."""
    if not self.validate_tempo():
      return

    self.status_text.config(state=tk.NORMAL)
    self.status_text.delete(1.0, tk.END)
    self.status_text.config(state=tk.DISABLED)

    self.processing_complete = False
    self.active_threads = 0
    self.processed_files = 0
    self.skipped_files = 0
    self.processed_files_set.clear()

    # Find, count and queue for processing all mp3 files
    self.queue_mp3_files()
    # Check, if there are no mp3 files to process
    if self.total_files == 0 or self.total_dst_sz_kb == 0:
      self.finish_processing(False)
      return

    # Remove existing progress bars, before creating new ones
    for progress_bar in self.progress_bars:
      progress_bar.grid_forget()
      progress_bar.destroy()
    self.progress_bars.clear()

    # Create progress bars dynamically
    n_progress_bars = min(self.total_files, self.n_threads.get())
    self.progress_bars = []
    for i in range(n_progress_bars):
      progress_bar = CustomProgressBar(self.master, width=1202, height=20)
      progress_bar.grid(row=9 + i, column=1)
      self.progress_bars.append(progress_bar)

    self.run_button.config(state=tk.DISABLED)
    for progress_bar in self.progress_bars:
      progress_bar.set_progress(0)
    self.total_progress.set_progress(0)
    self.total_progress.set_display_text("0/0 0%")

    self.start_time = time.time()
    msg = "Starting processing..."
    self.update_status(msg)
    logging.info(msg)
    self.start_process_files_threads()


  #############################################################################
  def update_status(self, message):
    """Updates the status text area."""
    self.status_text.config(state=tk.NORMAL)  # Enable editing
    self.status_text.insert(tk.END, message + "\n")
    self.status_text.see(tk.END)
    self.status_text.config(state=tk.DISABLED)  # Disable editing
    self.status_text.see(tk.END)  # Scroll to the bottom
    self.master.update_idletasks()


  #############################################################################
  def finish_processing(self, calc_time=True):
    """Handles processing completion."""
    if self.processing_complete == False:
      self.processing_complete = True
      if (calc_time == True):
        end_time = time.time()
        processing_time = end_time - self.start_time
      else:
        processing_time = 0
      if self.error_files == 0:
        total_dst_sz_mb = self.total_dst_sz_kb / 1024
        total_src_sz_mb = self.total_src_sz / (1024 * 1024)
        # Add the number of processed files
        msg = f"{self.processed_files} files processed"
        # Add the number of skipped files
        if self.skipped_files:
          msg += f" ({self.skipped_files} skipped)"
        # Add size
        msg += f", {total_dst_sz_mb:.2f} MB in "
        # Add time
        if processing_time < 60:
          msg += f"{processing_time:.2f} sec"
        else:
          msg += f"{int(processing_time/60)} min {int(processing_time%60)} sec"
        # Add compression ratio
        if total_dst_sz_mb:
          msg += f". Compression ratio {(total_src_sz_mb / total_dst_sz_mb):.2f}"
        self.update_status("\n" + msg)
        logging.info(msg)
      else:
        msg = f"\nProcessed / Total (Errors): {self.processed_files} / {self.total_files} ({self.error_files}) files in {processing_time:.2f} seconds"
        self.update_status(msg)
        logging.info(msg)
      self.run_button.config(state=tk.NORMAL)

      # Clear the threads list
      self.threads.clear()

      self.master.update_idletasks()


  #############################################################################
  def setup_logging(self, log_level='INFO'):
    """Sets up logging to a file."""
    log_file = 'tempo_log.txt'

    if log_level.upper() == 'DEBUG':
      logging.basicConfig(filename=log_file, level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s')
    else:  # INFO level
      logging.basicConfig(filename=log_file, level=logging.INFO, format='%(message)s')

    # Add separator and timestamp to the log file
    with open(log_file, 'a') as f:
      timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
      separator = f"\n\n==================== START OF LOG - {timestamp} ====================\n"
      f.write(separator)

    # Flush the log to ensure it's written
    for handler in logging.root.handlers:
      handler.flush()


  #############################################################################
  def validate_tempo(self):
    """Validates the tempo value."""
    try:
      tempo = float(self.tempo.get())
      if tempo <= 0 or tempo > 2:
        messagebox.showerror("Invalid Tempo", "Tempo must be greater than 0 and less than 2.")
        return False
      return True
    except ValueError:
      messagebox.showerror("Invalid Tempo", "Please enter a valid number for tempo.")
      return False


  #############################################################################
  def on_tempo_focusout(self, event):
    """Handles tempo entry focus out event, validating the input."""
    if not self.validate_tempo():
      self.tempo.set(self.tempo_default)  # Reset to default if invalid


  #############################################################################
  def process_status_updates(self):
    """Processes status updates from the queue."""
    while True:
      try:
        message = self.status_update_queue.get(timeout=GUI_TIMEOUT)  # Short timeout to avoid blocking indefinitely
        if message is None: # Check for exit signal
          break
        self.update_status(message)
        self.status_update_queue.task_done()
      except queue.Empty:
        continue
      except Exception as e:
        logging.exception("Error in status update thread: %s", e)
        break


###############################################################################
if __name__ == "__main__":
  root = tk.Tk()
  app = MP3Processor(root)
  root.mainloop()
