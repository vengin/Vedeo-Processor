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
import select

# Default values for the application
DFLT_FFMPEG_PATH = "d:/PF/_Tools/ffmpeg/bin/ffmpeg.exe"  # Change this if your ffmpeg path is different.
DFLT_TEMPO = 1.8
DFLT_N_THREADS = 4
DFLT_CONFIG_FILE = "tempo_config.ini"
GUI_TIMEOUT = 0.1
DFLT_BITRATE = 48 # i.e. 48K


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

    # Draw the outline rectangle
    self.outline_rect = self.create_rectangle(2, 2, width - 2, height - 2, outline="black")

    # Draw the filled progress rectangle
    self.progress_rect = self.create_rectangle(2, 2, fill_width + 2, height - 2, fill="#A8D8A8")

    # Draw the filename text
    self.text_id = self.create_text(width / 2, height / 2, text=self.filename_var.get(), fill="black")


  #############################################################################
  def set_progress(self, value):
    """Sets the progress value and redraws the bar."""
    self.progress_var.set(value)
    self.draw_progress_bar()


  #############################################################################
  def set_display_text(self, display_text):
    """Sets the filename to be displayed and redraws the bar."""
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
    self.overwrite_options = tk.StringVar()  # Initialize here
    self.use_compression_var = tk.BooleanVar()

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
    self.total_size = 0  # Total size of all files
    self.processed_size = 0  # Size of processed files
    self.processed_sizes_lock = threading.Lock()  # Lock for thread-safe access
    self.processed_sizes = [0] * DFLT_N_THREADS  # Initialize with 0 for each thread
    self.error_files = 0
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
    """Loads application configuration from tempo_config.ini."""
    config_file_read = self.config.read(DFLT_CONFIG_FILE)
    if not config_file_read:
      logging.info("Warning: Config file not found or corrupted. Using defaults.")
      self.config['DEFAULT'] = {
        'ffmpeg_path': DFLT_FFMPEG_PATH,
        'tempo': str(DFLT_TEMPO),
        'src_dir': '',
        'dst_dir': '',
        'n_threads': str(DFLT_N_THREADS),
        'overwrite_option': 'Skip existing files',  # Default option
        'use_compression': 'false',  # Default no compression
      }
    else:
      try:
        self.config['DEFAULT']['tempo'] = self.config['DEFAULT']['tempo'].split(';')[0].strip()
        # Load overwrite setting
        self.overwrite_options.set(self.config['DEFAULT'].get('overwrite_option', 'Skip existing files'))  # Load overwrite option
        # Load compression setting
        compression_setting = self.config['DEFAULT'].get('use_compression', 'false').lower()
        self.use_compression_var.set(compression_setting == 'true')

      except (KeyError, IndexError):
        messagebox.showwarning("Config Error",
          "Tempo value missing or malformed in config file. Using default.")
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
    self.config['DEFAULT']['overwrite_option'] = self.overwrite_options.get()  # Store overwrite option
    self.config['DEFAULT']['use_compression'] = str(self.use_compression_var.get()).lower()  # Store compression setting
    try:
      with open(DFLT_CONFIG_FILE, 'w') as configfile:
        self.config.write(configfile)
    except Exception as e:
      messagebox.showerror("Error", f"Could not save config file: {e}")


  #############################################################################
  def create_widgets(self):
    """Creates and arranges the GUI elements."""
    ttk.Label(self.master, text="Tempo:").grid(row=0, column=0, sticky=tk.W, padx=5)
    tempo_entry = ttk.Entry(self.master, textvariable=self.tempo, width=5)
    tempo_entry.grid(row=0, column=1, sticky=tk.W)
    tempo_entry.bind('<FocusOut>', self.on_tempo_focusout)

    # Source Directory Path
    ttk.Button(self.master, text="SrcDir", command=self.browse_src_dir).grid(row=1, column=0)
    ttk.Entry(self.master, textvariable=self.src_dir, width=200).grid(row=1, column=1, sticky=tk.W)

    # Destination Directory Path
    ttk.Button(self.master, text="DstDir", command=self.browse_dst_dir).grid(row=2, column=0)
    ttk.Entry(self.master, textvariable=self.dst_dir, width=200).grid(row=2, column=1, sticky=tk.W)

    ttk.Label(self.master, text="Number of threads:").grid(row=3, column=0, sticky=tk.W, padx=5)
    thread_values = list(range(1, 17))  # Creates a list from 1 to 16
    self.n_threads_combo = ttk.Combobox(self.master, textvariable=self.n_threads, values=thread_values, width=3, state="readonly")
    self.n_threads_combo.grid(row=3, column=1, sticky=tk.W)
    self.n_threads_combo.bind('<<ComboboxSelected>>', self.on_n_threads_change)

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

    # Create overall progress bar
    ttk.Label(self.master, text="Overall progress:").grid(row=8, column=0, sticky=tk.W, padx=5)
    self.total_progress = CustomProgressBar(self.master, width=1202, height=25)
    self.total_progress.grid(row=8, column=1, pady=10)  # Place it above progress bars for processed files


  #############################################################################
  def on_n_threads_change(self, event):
    """Handles changes in the number of threads combobox (currently does nothing)."""
    # This method can be used if you need to perform any action when the selection changes
    pass


  #############################################################################
  def browse_src_dir(self):
    """Opens a directory selection dialog for the source directory."""
    current_dir = self.src_dir.get()
    directory = filedialog.askdirectory(initialdir=current_dir if current_dir else None)
    if directory:  # Check if a directory was selected
      self.src_dir.set(os.path.normpath(directory))


  #############################################################################
  def browse_dst_dir(self):
    """Opens a directory selection dialog for the destination directory."""
    current_dir = self.dst_dir.get()
    directory = filedialog.askdirectory(initialdir=current_dir if current_dir else None)
    if directory:  # Check if a directory was selected
      self.dst_dir.set(os.path.normpath(directory))


  #############################################################################
  def get_mp3_info(self, ffmpeg_path, src_file_path):
    try:
      command = [ffmpeg_path, "-i", src_file_path, "-hide_banner"]
      process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      stdout, stderr = process.communicate()

      #  Example: "Duration: 00:01:25.49, start: 0.000000, bitrate: 69 kb/s"
      match = re.search(r"Duration: (\d{2}:\d{2}:\d{2}\.\d{2}), start: \d+\.\d+, bitrate: (\d+) kb\/s", stderr)
      if match:
        duration_str = match.group(1)
        minutes = int(duration_str.split(":")[1])
        seconds = int(duration_str.split(":")[2].split(".")[0])
        total_seconds = minutes*60 + seconds
        bitrate_kbps = int(match.group(2))
        return bitrate_kbps, total_seconds, True
      else:
        print(f"Error parsing ffmpeg output: {stderr}")
        return None, None, False

    except Exception as e:
      print(f"An error occurred in get_mp3_info: {e}")
      return None, None, False


  #############################################################################
  def handle_overwrite(self, dst_file_path, relative_path):
    """Handles overwrite logic based on user selection."""
    overwrite_option = self.overwrite_options.get()
    if os.path.exists(dst_file_path):
      if overwrite_option == "Overwrite existing files":  # Overwrite existing
        self.status_update_queue.put(f"Overwriting: {relative_path}")  # Use queue for status updates
        logging.debug(f"Overwriting: {relative_path}")
        return dst_file_path
      elif overwrite_option == "Rename existing files":  # Rename instead of overwriting
        base, ext = os.path.splitext(relative_path)
        i = 1
        while os.path.exists(os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")):
          i += 1
        dst_file_path = os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")
        self.status_update_queue.put(f"Renaming: {relative_path} to {os.path.basename(dst_file_path)}")  # Use queue for status updates
        logging.debug(f"Renaming: {relative_path} to {os.path.basename(dst_file_path)}")
        return dst_file_path
      elif overwrite_option == "Skip existing files":  # Skip processing
        self.status_update_queue.put(f"Skipping: {relative_path}")  # Use queue for status updates
        logging.debug(f"Skipping: {relative_path}")
        return None  # Skip processing this file
    else:  # Normal output (no overwrite)
      self.status_update_queue.put(f"Processing: {relative_path}")  # Use queue for status updates
      logging.debug(f"Processing: {relative_path}")
      return dst_file_path


  #############################################################################
  def generate_ffmpeg_command(self, src_file_path, dst_file_path, bit_rate):
    """Generates the FFMPEG command with tempo and optional compression."""
    ffmpeg_command = [
      self.ffmpeg_path.get(),
      "-i", src_file_path,
      "-filter:a", f"atempo={self.tempo.get()}",  # audio filter
      "-vn",  # Disable video stream
      "-b:a", f"{bit_rate}k", # Fixed Bit-Rate
      dst_file_path,
      "-y",  # Force overwrite output file
    ]

    self.use_compression = self.use_compression_var.get()
    if self.use_compression:
      ffmpeg_compression_params = [
        "-codec:a", "libmp3lame",  # LAME (Lame Ain't an MP3 Encoder) MP3 encoder wrapper
        "-q:a", "7",  # quality setting for VBR
        "-ar", "22050"  # sample rate
      ]
      # Insert after "src_file_path" before "-filter:a"
      ffmpeg_command[3:3] = ffmpeg_compression_params

    logging.debug(f"FFMPEG command: {' '.join(ffmpeg_command)}")
    return ffmpeg_command


  #############################################################################
  def monitor_progress(self, process, progress_bar, dst_est_sz_kbt, thread_index):
    """Monitors FFMPEG progress for each mp3 file and updates the progress bar."""
    q = queue.Queue()

    def read_stderr(p, q):
      for line in iter(p.stderr.readline, ''):
        q.put(line)
      q.put(None)

    stderr_thread = threading.Thread(target=read_stderr, args=(process, q))
    stderr_thread.daemon = True
    stderr_thread.start()

    processed_size_kb = 0  # Initialize in KiB
    try:
      while True:
        try:
          line = q.get(timeout=GUI_TIMEOUT)
          if line is None:
            break
          match = re.search(r"size=\s*(\d+)\w+", line)
          if match:
            processed_size_kb = int(match.group(1))  # in KiB
            progress = min(100, (processed_size_kb / dst_est_sz_kbt) * 100)
            progress_bar.set_progress(progress)
#            self.update_overall_progress() # Update total progress after each file
            self.master.update_idletasks()

            with self.processed_sizes_lock:
              self.processed_sizes[thread_index] = processed_size_kb
            self.update_overall_progress()

        except queue.Empty:
          if process.poll() is not None:
            break
          time.sleep(GUI_TIMEOUT)

    except Exception as e:
      logging.exception(f"Error monitoring progress: {e}")

    finally:
      progress_bar.set_progress(100)
      self.master.update_idletasks()
      stderr_thread.join()
    return processed_size_kb


  #############################################################################
  def update_overall_progress(self):
    """Updates the overall progress bar based on cumulative processed size."""
    with self.processed_sizes_lock:
      total_processed_size_kb = sum(self.processed_sizes)
    total_progress_percentage = int((total_processed_size_kb / self.total_size) * 100) if self.total_size > 0 else 0
    print(f"ttl_prcssd_sz_kb={total_processed_size_kb}, ttl_sz={self.total_size}, prgrss={total_progress_percentage}")
    total_progress_message = f"{self.processed_files}/{self.total_files} {total_progress_percentage}%"
    self.total_progress.set_progress(total_progress_percentage)
    self.total_progress.set_display_text(total_progress_message)

    # All files processed?
    if self.processed_files == self.total_files:
      total_progress_percentage = 100
      total_progress_message = f"{self.processed_files}/{self.total_files} {total_progress_percentage}%"
      self.total_progress.set_progress(total_progress_percentage)
      self.total_progress.set_display_text(total_progress_message)
      self.master.after(100, self.finish_processing)


  #############################################################################
  def process_file(self, src_file_path, relative_path, progress_bar, thread_index):
    """Processes a single MP3 file, handling potential overwrites."""
    if relative_path in self.processed_files_set:
      return  # Skip if already processed

    self.processed_files_set.add(relative_path)
    try:
      dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
      dst_file_path = self.handle_overwrite(dst_file_path, relative_path)
      if dst_file_path is None:
        return  # Skip if file should be skipped

      os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)

      # Get source file bitrate and duration, to calculate dst file estimate size
      src_bitrate, duration, success = self.get_mp3_info(self.ffmpeg_path.get(), src_file_path)
      if not success:
        raise Exception("Could not get MP3 info")
      dst_bitrate = min(DFLT_BITRATE, src_bitrate)
      # Calculate Estimated output size in KiB divided by Tempo
      dst_est_sz_kbt = int(dst_bitrate * duration / (8 * self.tempo.get())) #in KiB
      # Add to totall size
      self.total_size += dst_est_sz_kbt
      print(f"dst_est_sz_kbt={dst_est_sz_kbt}, ttl_sz={self.total_size}")

      # Generate ffmpeg command with tempo and optional compression
      ffmpeg_command = self.generate_ffmpeg_command(src_file_path, dst_file_path, dst_bitrate)

      src_size = os.path.getsize(src_file_path)
      progress_bar.set_display_text(os.path.basename(dst_file_path)) # Set filename

      # Start individual FFMPEG process for each file (n_threads) and
      process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      # Monitor and update each mp3 file processing progress
      self.monitor_progress(process, progress_bar, dst_est_sz_kbt, thread_index)

      self.master.update_idletasks()
#      self.update_overall_progress() # Update total progress after each file

    except FileNotFoundError:
      logging.error(f"FFMPEG not found or invalid path: {self.ffmpeg_path.get()}")
      self.status_update_queue.put(f"Error: FFMPEG not found for {relative_path}")  # Use queue for status updates
      self.error_files += 1
    except subprocess.CalledProcessError as e:
      logging.error(f"ffmpeg error processing {relative_path}: return code {e.returncode}, output: {e.stderr}")
      self.status_update_queue.put(f"Error processing: {relative_path}")  # Use queue for status updates
      self.error_files += 1
    except Exception as e:
      logging.exception(f"An unexpected error occurred processing {relative_path}: {e}")
      self.status_update_queue.put(f"Error: Unexpected issue with {relative_path}")  # Use queue for status updates
      self.error_files += 1
    finally:
      self.processed_files += 1
      self.update_overall_progress() # Update total progress after each file
      # # Update overall progress bar
      # total_progress_percentage = int((self.processed_size / self.total_size) * 100) if self.total_size > 0 else 0
      # total_progress_message = f"{self.processed_files}/{self.total_files} {total_progress_percentage}%"
      # self.total_progress.set_progress(total_progress_percentage)
      # self.total_progress.set_display_text(total_progress_message)

      # # All files processed?
      # if self.processed_files == self.total_files:
      #   self.total_progress.set_progress(100)
      #   self.master.after(100, self.finish_processing)

  #############################################################################
  def queue_mp3_files(self):
    """Find, count and queue for processing all mp3 files in all sub-directories"""
    src_dir = self.src_dir.get()
    self.total_files = 0
    self.queue = queue.Queue()
    self.processed_files_set.clear()

    self.total_size = 0
    self.processed_size = 0
    for root, _, files in os.walk(src_dir):
      for file in files:
        if file.lower().endswith(".mp3"):
          full_path = os.path.join(root, file)
          relative_path = os.path.relpath(full_path, src_dir)
          self.queue.put((full_path, relative_path))
          self.total_files += 1
#          self.total_size += os.path.getsize(full_path)
    logging.debug(f"total_files: {self.total_files}")


  #############################################################################
  def process_files(self):
    """Processes all MP3 files in the source directory using multiple threads."""
#    self.queue_mp3_files()

#     src_dir = self.src_dir.get()
#     self.total_files = 0
#     self.queue = queue.Queue()
#     self.processed_files_set.clear()

#     self.total_size = 0
#     self.processed_size = 0
#     for root, _, files in os.walk(src_dir):
#       for file in files:
#         if file.lower().endswith(".mp3"):
#           full_path = os.path.join(root, file)
#           relative_path = os.path.relpath(full_path, src_dir)
#           self.queue.put((full_path, relative_path))
#           self.total_files += 1
# #          self.total_size += os.path.getsize(full_path)
#     logging.debug(f"total_files: {self.total_files}, Total file size: {self.total_size}")

    self.start_threads()


  #############################################################################
  def start_threads(self):
    """Starts the worker threads to process the files."""
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
        self.process_file(file_path, relative_path, self.progress_bars[thread_index], thread_index)
        self.queue.task_done()
      except queue.Empty:
        break
      except Exception as e:
        self.status_update_queue.put(f"Error in thread {thread_index + 1}: {e}")
        logging.exception(f"Error in thread {thread_index + 1}")
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

    # Remove existing progress bars, before creating new ones
    for progress_bar in self.progress_bars:
      progress_bar.grid_forget()
      progress_bar.destroy()
    self.progress_bars.clear()

    # Find, count and queue for processing all mp3 files
    self.queue_mp3_files()
    n_progress_bars = min(self.total_files, self.n_threads.get())

    # Create progress bars dynamically
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
    self.active_threads = 0
    self.processed_files = 0
    self.start_time = time.time()
    self.processing_complete = False
    self.processed_files_set.clear()
    self.status_text.config(state=tk.NORMAL)
    self.status_text.delete(1.0, tk.END)
    self.status_text.config(state=tk.DISABLED)
    self.update_status("Starting processing...")
    logging.info("Starting processing...")

    self.process_files()


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
  def finish_processing(self):
    """Handles the completion of processing."""
    if not self.processing_complete:
      self.processing_complete = True
      end_time = time.time()
      processing_time = end_time - self.start_time
      if self.error_files == 0:
        if processing_time < 60:
          msg = f"{self.processed_files} files processed in {processing_time:.2f} sec"
        else:
          msg = f"{self.processed_files} files processed in {int(processing_time/60)} min {int(processing_time%60)} sec"
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
