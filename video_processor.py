import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from tkinter import scrolledtext
from tkinter import messagebox
from datetime import datetime
import json
import configparser
import os
import subprocess
import threading
import queue
import time
import logging
import re
import psutil

# Default values for the application
DFLT_FFMPEG_PATH = "d:/PF/_Tools/ffmpeg/bin/ffmpeg.exe"  # Change this if your ffmpeg path is different.
DFLT_SRC_DIR = ""
DFLT_DST_DIR = ""
DFLT_TEMPO = 1.0
DFLT_N_THREADS = 4
DFLT_N_THREADS_MAX = 16
DFLT_CONFIG_FILE = "video_processor_config.ini"
DFLT_LOG_FILE = "video_processor.log"
VID_EXT = ('.mp4', '.mkv', 'avi', '.webm', '.flv', '.wmv')
DFLT_OVERWRITE_OPTION = "Skip existing files"  # Skip by default
GUI_TIMEOUT = 0.3 # in seconds
UPDATE_STATUS_TIMEOUT = 1 # in seconds


#############################################################################
class CustomProgressBar(tk.Canvas):
  """
  Custom progress bar class for displaying processing progress.
  Inherits from tkinter Canvas widget.
  """
  def __init__(self, master, use_bold_font=False, *args, **kwargs):
    super().__init__(master, *args, **kwargs)
    self.progress_var = tk.DoubleVar()
    self.filename_var = tk.StringVar()
    self.paused = tk.BooleanVar(value=False)
    self.cancelled = tk.BooleanVar(value=False)
    self.relative_path = None

    # Set bald font based on parameter
    self.text_font = ('TkDefaultFont', 9, 'bold') if use_bold_font else ('TkDefaultFont', 9)

    # Bind configure event to handle resizing
    self.bind("<Configure>", self.draw_progress_bar)

    # Initial draw
    self.draw_progress_bar()


  #############################################################################
  def draw_progress_bar(self, event=None):
    """Redraws the progress bar based on current progress and filename."""
    self.delete("all")  # Clear canvas

    # Get current dimensions
    width = self.winfo_width()
    height = self.winfo_height()

    # Calculate progress width
    progress = self.progress_var.get()
    fill_width = int((width - 5) * (progress / 100))  # Adjusted for border

    # Draw border rectangle first
#    self.create_rectangle(2, 2, width-2, height-2,  outline="black", width=1)
    self.create_rectangle(2, 2, width-2, height-2,  outline="black")

    # Draw progress fill inside the border
    if fill_width > 0:
      fill_color = "#A8D8A8"  # Default green
      if self.paused.get():
        fill_color = "#F8EA90"  # Yellow for paused
      if self.cancelled.get():
        fill_color = "#FF9999"  # Red for cancelled
      self.create_rectangle(2, 2, fill_width + 2, height - 2, fill=fill_color)

    # Draw centered text
    self.create_text(
      width / 2, height / 2,
      text=self.filename_var.get(),
      anchor="center",
      fill="black",
      font=self.text_font  # Bald font (optionally)
    )


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
class VideoProcessor:
  """
  Main class for the Video Compression Processor application.
  Handles GUI interaction, configuration, and processing logic.
  """
  def __init__(self, master):
    self.master = master
    master.title("Video Compression Processor")

    # Pre-define elements\variables (to avoid linter warnings and errors)
    self.run_button = None
    self.overwrite_options = tk.StringVar(value=DFLT_OVERWRITE_OPTION)

    # Initialize GUI variables as empty
    self.ffmpeg_path = tk.StringVar()
    self.tempo = tk.DoubleVar()
    self.src_dir = tk.StringVar()
    self.dst_dir = tk.StringVar()
    self.n_threads = tk.IntVar()

    # Load application configuration
    self.config = configparser.ConfigParser()
    self.load_config()

    # Init variables
    self.progress_bars = []
    self.progress_bars_idx = []
    self.active_threads = 0
    self.total_files = 0
    self.processed_files = 0
    self.processed_files_lock = threading.Lock()  # Lock for thread-safe access
    self.processed_seconds_arr = {}
    self.processed_seconds_arr_lock = threading.Lock()  # Lock for thread-safe access
    self.total_dst_seconds = 0  # Total size of all files
    self.total_dst_seconds_lock = threading.Lock()  # Lock for thread-safe access
    self.total_dst_sz = 0
    self.total_src_sz = 0
    self.error_files = 0
    self.skipped_files = 0
    self.cancelled_files = 0
    self.status_text = None
    self.start_time = None
    self.processing_complete = False
    self.processed_files_set = set()
    self.processing_complete_event = threading.Event()
    self.active_processes = {}  # Change to a dictionary {pid: process_object}
    self.processes_lock = threading.Lock()  # Add lock for thread-safe access
    self.progress_bar_to_pid = {}  # Maps progress bar to process pid

    # Create GUI elements
    self.create_widgets()
    # Initialize threading components
    self.queue = queue.Queue()
    self.gui_queue = queue.Queue()  # Queue for GUI updates
    self.threads = []

    # Bind the save_config method to the window close event.
    self.master.protocol("WM_DELETE_WINDOW", self.on_closing)

    self.setup_logging('INFO')  # 'INFO' or 'DEBUG' for more detailed logging
    logging.info("VideoProcessor initialized")

    self.status_update_queue = queue.Queue()
    self.status_update_thread = threading.Thread(target=self.process_status_updates, daemon=True) # Explicitly set daemon
    self.status_update_thread.start()
    logging.info("Status update thread started.")

    # Using this flag for more gracefull shutdown, if closing application while files are still processed
    self.is_shutting_down = False


  #############################################################################
  def load_config(self):
    """Loads config from video_processor_config.ini or uses defaults if not found."""
    if not self.config.read(DFLT_CONFIG_FILE):
      logging.warning("Config file not found. Using defaults.")
      self.config['DEFAULT'] = {
        'ffmpeg_path': DFLT_FFMPEG_PATH,
        'tempo': str(DFLT_TEMPO),
        'src_dir': DFLT_SRC_DIR,
        'dst_dir': DFLT_DST_DIR,
        'n_threads': str(DFLT_N_THREADS),
        'overwrite_option': DFLT_OVERWRITE_OPTION,  # Skip by default
      }
    else:
      try:
        # Set the values using the loaded configuration or defaults
        self.ffmpeg_path.set(self.config['DEFAULT'].get('ffmpeg_path', DFLT_FFMPEG_PATH))
        self.tempo.set(float(self.config['DEFAULT'].get('tempo', str(DFLT_TEMPO))))
        self.src_dir.set(self.config['DEFAULT'].get('src_dir', DFLT_SRC_DIR))
        self.dst_dir.set(self.config['DEFAULT'].get('dst_dir', DFLT_DST_DIR))
        self.n_threads.set(int(self.config['DEFAULT'].get('n_threads', str(DFLT_N_THREADS))))
        self.overwrite_options.set(self.config['DEFAULT'].get('overwrite_option', DFLT_OVERWRITE_OPTION))
      except Exception as e:
        messagebox.showerror("Config Error", f"Could not load config file: {e}")


  #############################################################################
  def save_config(self):
    """Saves application configuration to video_processor_config.ini."""
    if self.validate_tempo():
      self.config['DEFAULT']['tempo'] = str(self.tempo.get())
    else:
      self.config['DEFAULT']['tempo'] = str(DFLT_TEMPO)

    self.config['DEFAULT']['n_threads'] = str(self.n_threads.get())
    self.config['DEFAULT']['ffmpeg_path'] = self.ffmpeg_path.get()
    self.config['DEFAULT']['src_dir'] = self.src_dir.get()
    self.config['DEFAULT']['dst_dir'] = self.dst_dir.get()
    self.config['DEFAULT']['overwrite_option'] = self.overwrite_options.get()
    try:
      with open(DFLT_CONFIG_FILE, 'w') as configfile:
        self.config.write(configfile)
    except Exception as e:
      messagebox.showerror("Config Error", f"Could not save config file: {e}")


  #############################################################################
  def create_widgets(self):
    """Creates and arranges GUI elements."""
    # Tempo
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

    # Number of threads 1-DFLT_N_THREADS_MAX
    ttk.Label(self.master, text="Number of threads:").grid(row=3, column=0, sticky=tk.W, padx=5)
    n_thread_values = list(range(1, DFLT_N_THREADS_MAX+1))  # Creates a list from 1 to DFLT_N_THREADS_MAX
    self.n_threads_combo = ttk.Combobox(self.master, textvariable=self.n_threads, values=n_thread_values, width=3, state="readonly")
    self.n_threads_combo.grid(row=3, column=1, sticky=tk.W)

    # Overwrite choice
    ttk.Label(self.master, text="File Overwrite Options:").grid(row=4, column=0, sticky=tk.W, padx=5)
    self.overwrite_options_combobox = ttk.Combobox(self.master,
      textvariable=self.overwrite_options,
      values=[ "Skip existing files", "Overwrite existing files", "Rename existing files"],
      state="readonly")
    self.overwrite_options_combobox.grid(row=4, column=1, sticky=tk.W)

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
  def get_metadata_info(self, ffmpeg_path, src_file_path):
    """Gets media file metadata (Duration) using FFPROBE."""
    try:
      # Derive ffprobe_path from ffmpeg_path
      ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe.exe")
      ffprobe_cmd = [
        ffprobe_path, '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        src_file_path
      ]
      rslt = subprocess.run(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
      info = json.loads(rslt.stdout)
      total_seconds = int(float(info['format']['duration']))  # seconds

    except Exception as e:
      logging.error(f"Error getting Tag info from {src_file_path}: {e}")
      return None, None, False

    return total_seconds, True


  #############################################################################
  def handle_overwrite(self, dst_file_path, relative_path):
    """Handles overwrite logic based on user selection."""
    msg = ""
    overwrite_option = self.overwrite_options.get()
    dst_relative_path_base, ext = os.path.splitext(relative_path)
    dst_relative_path = dst_relative_path_base + ext
    dst_file_path = os.path.join(self.dst_dir.get(), dst_relative_path)
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
  def generate_ffmpeg_command(self, src_file_path, dst_file_path):
    """Generates FFMPEG command for compression with optional tempo."""
    # Convert paths to string and handle potential encoding issues
    src_file_path = str(src_file_path)
    base, ext = os.path.splitext(str(dst_file_path))
    dst_file_path = base + ext

    # Cmd example:
    # ffmpeg.exe -i i.mp4 -filter:v setpts=0.66666667*PTS,scale=640:360 -filter:a atempo=1.5 -vf scale=640:360 -pix_fmt yuv420p -c:v libaom-av1 -b:v 70k -crf 30 -cpu-used 8 -row-mt 1 -g 240 -aq-mode 0 -c:a aac -b:a 80k o.mp4 -y -progress pipe:1 -nostats -hide_banner -loglevel error
    ffmpeg_command = [
      str(self.ffmpeg_path.get()),
      # General options
      "-i", src_file_path,
      # Filter options
      "-vf", "scale=640:360",
      "-pix_fmt", "yuv420p",
      # Video options
      "-c:v", "libaom-av1",
      "-b:v", "70k",
      "-crf", "30",
      "-cpu-used", "8",
      "-row-mt", "1",
      "-g", "240",
      "-aq-mode", "0",
      # Audio options
      "-c:a", "aac",
      "-b:a", "80k",
      # Output options
      dst_file_path,
      "-y",  # Force overwrite output file
      # Progress reporting
      "-progress", "pipe:1", # Pipe progress to stdout
      "-nostats", # Disable default stats output
      # Logging options
      "-hide_banner",
      "-loglevel", "error",
    ]

    if self.tempo.get() != 1.0:
      # If tempo is not 1, we need to adjust both video and audio streams
      # For video files we need to use tempo value for audio stream and PTS=1/tempo for video
      PTS = 1 / self.tempo.get() # PTS is 1/tempo
      ffmpeg_tempo_params = [
        "-filter:v", f"setpts={PTS:.8f}*PTS,scale=640:360",
        "-filter:a", f"atempo={self.tempo.get()}",  # tempo audio filter
      ]
      # Replace ["-vf", "scale=640:360"], use single combined video filter
      # Cmd example:
      # ffmpeg.exe -i i.mp4 -filter:v setpts=0.66666667*PTS,scale=640:360 -filter:a atempo=1.5 -vf scale=640:360 -pix_fmt yuv420p -c:v libaom-av1 -b:v 70k -crf 30 -cpu-used 8 -row-mt 1 -g 240 -aq-mode 0 -c:a aac -b:a 80k o.mp4 -y -progress pipe:1 -nostats -hide_banner -loglevel error
      ffmpeg_command[3:5] = ffmpeg_tempo_params


    logging.debug(f"Process File: FFMPEG command: {' '.join(ffmpeg_command)}")
    return ffmpeg_command


  #############################################################################
  def monitor_progress(self, process, progress_bar, dst_time, relative_path):
    """Monitors FFMPEG progress by reading stdout and updates the progress bar."""
    q = queue.Queue()

    def read_stdout(p, q):
      while True:
        line = p.stdout.readline()
        if not line:
          break
        q.put(line.decode('utf-8', errors='replace'))
      q.put(None)

    stdout_thread = threading.Thread(target=read_stdout, args=(process, q))
    stdout_thread.daemon = True
    stdout_thread.start()

    try:
      while True:
        try:
          line = q.get(timeout=GUI_TIMEOUT)
          if line is None:
            break
#          logging.debug(f"Progress line: {line.strip()}")

          # Example output
          ########
          # frame=5
          # fps=0.00
          # stream_0_0_q=0.0
          # bitrate=  56.9kbits/s
          # total_size=1482
          # out_time_us=208542
          # out_time_ms=208542
          # out_time=00:00:00.208542
          # dup_frames=0
          # drop_frames=0
          # speed=0.407x
          # progress=continue
          ########
          if "out_time_ms=" in line:
            parts = line.strip().split('=')
            if len(parts) == 2 and parts[0] == 'out_time_ms':
              if parts[1] == 'N/A':
                continue
              try:
                processed_us = int(parts[1])
                processed_seconds = processed_us / 1_000_000.0

                with self.processed_seconds_arr_lock:
                  self.processed_seconds_arr[relative_path] = processed_seconds

                progress = min(100, (processed_seconds / dst_time) * 100) if dst_time > 0 else 0
                progress_bar.set_progress(progress)
                self.master.update_idletasks()
                self.update_total_progress()
                logging.debug(f"processed_us={processed_us}, processed_seconds/dst_time = {processed_seconds:.1f}/{dst_time:.1f} = {(processed_seconds / dst_time * 100):.1f}" )
              except (ValueError, IndexError) as e:
                logging.warning(f"Could not parse progress line: {line.strip()} - {e}")

        except queue.Empty:
          if process.poll() is not None:
            break
          time.sleep(GUI_TIMEOUT)

    except Exception as e:
      logging.exception(f"Error monitoring progress for {relative_path}: {e}")
    finally:
      # Check if the process was cancelled
      if not progress_bar.cancelled.get():
        progress_bar.set_progress(100)
        with self.processed_files_lock:
          self.processed_files += 1
      self.master.update_idletasks()
      stdout_thread.join()
    return


  #############################################################################
  def update_total_progress(self):
    """Updates the total progress bar based on cumulative processed size."""
    if self.is_shutting_down:
      return

    current_time = time.time()

    # Only update GUI at specified intervals >= GUI_TIMEOUT
    if not hasattr(self, '_last_progress_update') or \
       (current_time - self._last_progress_update) >= GUI_TIMEOUT:
      self._last_progress_update = current_time

      # Update processed time under lock
      with self.total_dst_seconds_lock:
        total_processed_seconds = sum(self.processed_seconds_arr.values())
      total_progress_percentage = int((total_processed_seconds / self.total_dst_seconds) * 100) if self.total_dst_seconds > 0 else 0
      total_progress_percentage = min(100, total_progress_percentage)
      logging.debug(f"ttl_prcssd_seconds={int(total_processed_seconds)}, ttl_seconds={int(self.total_dst_seconds)}, prgrss={total_progress_percentage}")

      total_progress_message = f"{total_progress_percentage}%  {self.processed_files+self.skipped_files + self.cancelled_files}/{self.total_files}"

      # Wrap GUI updates in try-except
      try:
        self.total_progress.set_progress(total_progress_percentage)
        self.total_progress.set_display_text(total_progress_message)
      except tk.TclError:
        logging.debug("GUI already closed, skipping progress update")
        return

      # When all files processed, set progress to 100% (might be a bit smaller/larger otherwise)
      if self.processed_files + self.skipped_files + self.cancelled_files == self.total_files:
        total_progress_message = f"100%  {self.processed_files+self.skipped_files+self.cancelled_files}/{self.total_files}"
        self.total_progress.set_progress(100)
        self.total_progress.set_display_text(total_progress_message)
        try:
          self.master.after(100, self.finish_processing)
        except tk.TclError:
          logging.debug("GUI already closed, skipping final progress update")



  #############################################################################
  def process_file(self, src_file_path, relative_path, progress_bar):
    """Processes a single audio file, handling potential overwrites."""

    if relative_path in self.processed_files_set:
      return  # Skip if already processed

    self.processed_files_set.add(relative_path)
    process = None  # Define process outside try block
    try:
      # if dst_file_path is None:  # Skip file
      if self.file_info[relative_path]["skipped"]:
        progress_bar.set_display_text(relative_path)
        progress_bar.set_progress(100)
        return  # Do not process, if the file should be skipped

      dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
      dst_file_path = self.handle_overwrite(dst_file_path, relative_path)
      os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)

      # Get pre-calculated file info
      file_data = self.file_info[relative_path]
      dst_time = file_data["duration"]

      # Display processed filename in progress bar
      progress_bar.set_display_text(os.path.basename(dst_file_path))
      progress_bar.relative_path = relative_path

      # Generate ffmpeg command for video compression
      ffmpeg_command = self.generate_ffmpeg_command(src_file_path, dst_file_path)
      # Start FFMPEG process in binary mode for each file (n_threads)
      process = subprocess.Popen(
        ffmpeg_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # The progress is piped to stdout, so we need to make sure the stderr buffer doesn't fill up
        # We can read it to a devnull to discard it.
        # Note: This requires universal_newlines=False, which is the default.
        # stderr=subprocess.DEVNULL
      )
      # Add process to active processes list
      with self.processes_lock:
        self.active_processes[process.pid] = process
        self.progress_bar_to_pid[progress_bar] = process.pid

      # Monitor and update each audio file processing progress
      self.monitor_progress(process, progress_bar, dst_time, relative_path)
      self.master.update_idletasks()

      # Remove process from active processes list
      with self.processes_lock:
        if process.pid in self.active_processes:
          del self.active_processes[process.pid]
        if progress_bar in self.progress_bar_to_pid:
          del self.progress_bar_to_pid[progress_bar]


    except Exception as e:
      msg = f"Error processing {relative_path}: {e}"
      logging.exception(msg)
      self.status_update_queue.put(msg)
      self.error_files += 1
      raise
    finally:
      # Ensure process is removed from active processes even if error occurs
      with self.processes_lock:
        if process and process.pid in self.active_processes:
          del self.active_processes[process.pid]
        if progress_bar in self.progress_bar_to_pid:
            del self.progress_bar_to_pid[progress_bar]
      if not self.is_shutting_down:
        self.update_total_progress() # Update total progress after each file


  #############################################################################
  def count_dst_files_sz(self):
    """Calculate the actual size of output files after processing."""
    dst_dir = self.dst_dir.get()
    n_files = 0
    self.total_dst_sz = 0

    for root, _, files in os.walk(dst_dir):
      for file in files:
        if file.lower().endswith(VID_EXT):
          full_path = os.path.join(root, file)
          self.total_dst_sz += os.path.getsize(full_path)
          n_files += 1


  #############################################################################
  def queue_media_files(self):
    """Find, count, queue video files, and pre-calculate output sizes."""
    src_dir = self.src_dir.get()
    self.total_files = 0
    self.queue = queue.Queue()
    self.file_info = {}  # Dictionary to store file info
    self.processed_files_set.clear()
    self.total_dst_seconds = 0

    last_update_time = time.time()

    for root, _, files in os.walk(src_dir):
      for file in files:
        if file.lower().endswith(VID_EXT):
          full_path = os.path.join(root, file)
          relative_path = os.path.relpath(full_path, src_dir)
          self.queue.put((full_path, relative_path))
          self.total_files += 1
          self.total_src_sz += os.path.getsize(full_path)

          # Skip existing files
          overwrite_option = self.overwrite_options.get()
          dst_relative_path_base, ext = os.path.splitext(relative_path)
          dst_file_path = os.path.join(self.dst_dir.get(), dst_relative_path_base + ext)
          if os.path.exists(dst_file_path) and overwrite_option == "Skip existing files":
            self.skipped_files += 1
            self.file_info[relative_path] = {"duration": 0, "skipped": True}
          else:
            # Get audio file metadata and calculate size
            duration, success = self.get_metadata_info(self.ffmpeg_path.get(), full_path)
            if success:
              duration_tempo = duration/self.tempo.get()
              self.file_info[relative_path] = {"duration": duration_tempo, "skipped": False}
              # logging.debug(f"{relative_path}: dst_est_sz_kbt={dst_est_sz_kbt}")
              dst_seconds = int(duration_tempo)
              self.total_dst_seconds += dst_seconds
              logging.debug(f"{relative_path}: dst_seconds={dst_seconds}")

            else:
              logging.error(f"Could not get audio file metadata for {full_path}")
              self.error_files += 1

          # Update the status_text every second, replacing text (instead of adding new lines)
          current_time = time.time()
          if current_time - last_update_time >= UPDATE_STATUS_TIMEOUT:
            msg = f"{self.total_files} files analyzed, total duration: "
            if (self.total_dst_seconds > 3600):  # > 1 Hour?
              msg += f"{self.total_dst_seconds / (3600):.2f} Hours"
            else:
              msg += f"{self.total_dst_seconds / (60):.2f} Minutes"
            self.update_status(msg, replace=True)
#            logging.info(msg)
            self.master.update_idletasks()
            last_update_time = current_time

    logging.debug(f"total_dst_seconds={self.total_dst_seconds}")
    msg = f"{self.total_files} files analyzed, total duration: "
    if (self.total_dst_seconds > 3600):  # > 1 Hour?
      msg += f"{self.total_dst_seconds / (3600):.2f} Hours"
    else:
      msg += f"{self.total_dst_seconds / (60):.2f} Minutes"

    self.update_status(msg, replace=True)
    logging.info(msg)


  #############################################################################
  def start_process_files_threads(self):
    """Starts the file processing threads."""
    num_threads = min(self.n_threads.get(), self.total_files)
    self.active_threads = num_threads

    for i in range(num_threads):
      thread = threading.Thread(target=self.worker, args=(i,), name=f"Worker-{i}")
      thread.daemon = True  # Make thread daemon so it doesn't prevent program exit
      self.threads.append(thread)
      thread.start()


  #############################################################################
  def worker(self, thread_index):
    """Worker function for each thread, processing files from the queue."""
    progress_bar = self.progress_bars[thread_index]
    progress_bar.cancelled.set(False)
    progress_bar.draw_progress_bar()

    while not self.is_shutting_down:  # Check shutdown flag
      try:
        # Reduced timeout to make thread more responsive to shutdown
        file_path, relative_path = self.queue.get(timeout=0.1)

        # Check shutdown flag immediately after getting item
        if file_path is None or self.is_shutting_down:
          self.queue.task_done()
          break

        self.process_file(file_path, relative_path, progress_bar)
        self.queue.task_done()

        # Check if this was the last file
        if len(self.processed_files_set) >= self.total_files:
          break

      except queue.Empty:
        # Check if all files are processed
        if len(self.processed_files_set) >= self.total_files:
          break
        continue
      except Exception as e:
        if not self.is_shutting_down:
          msg = f"Error in worker {thread_index}: {e}"
          self.status_update_queue.put(msg)
          logging.exception(msg)
        self.queue.task_done()  # Ensure task is marked as done even on error
        break

    with threading.Lock():  # Use a lock to safely decrement active_threads
      self.active_threads -= 1
      if self.active_threads == 0 and not self.is_shutting_down:
        try:
          self.master.after(100, self.finish_processing)
        except tk.TclError:
          logging.debug("GUI already closed, skipping finish_processing call")


  #############################################################################
  def on_closing(self):
    """Handles window closing event, saving configuration."""
    logging.info("Starting application shutdown sequence...")
    self.is_shutting_down = True
    self.save_config()

    # Kill all FFMPEG processes first
    self.kill_active_processes()

    # Clear the file processing queue and signal threads to stop
    queue_items = 0

    # Clear queue and signal threads in one pass
    while not self.queue.empty():
      try:
        self.queue.get_nowait()
        self.queue.task_done()
        queue_items += 1
      except queue.Empty:
        break

    # Add sentinel values for remaining threads
    for _ in range(len(self.threads)):
      self.queue.put((None, None))

    # Wait for threads with shorter timeout
    for thread in self.threads:
      thread.join(timeout=0.01)
      if thread.is_alive():
        logging.warning(f"Worker thread {thread.name} failed to stop gracefully")

    # Clear status update queue
    while not self.status_update_queue.empty():
      try:
        self.status_update_queue.get_nowait()
        self.status_update_queue.task_done()  # Mark task as done
      except queue.Empty:
        break

    # Stop status update thread
    self.status_update_queue.put(None)
    try:
      self.status_update_thread.join(timeout=0.2)
      if self.status_update_thread.is_alive():
        logging.warning("Status update thread failed to stop gracefully")
    except Exception as e:
      logging.error(f"Error joining status update thread: {e}")

    self.master.destroy()
    logging.info("Application shutdown complete")


  #############################################################################
  def start_processing(self):
    """Starts the audio processing."""
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

    # Remove existing progress bars, before creating new ones
    for progress_bar in self.progress_bars:
      progress_bar.grid_forget()
      progress_bar.destroy()
    self.progress_bars.clear()

    # Remove index labels (used to index progress/threads), before creating new ones
    for label in self.progress_bars_idx:
      label.grid_forget()
      label.destroy()
    self.progress_bars_idx.clear()

    # Find, count and queue for processing all audio files
    self.queue_media_files()
    # Check, if there are no audio files to process
    if self.total_files == 0:
      self.finish_processing(False)
      return

    # Create progress bars dynamically
    n_progress_bars = min(self.total_files, self.n_threads.get())
    self.progress_bars = []
    self.progress_bars_idx = []
    for i in range(n_progress_bars):
      # Create index label
      idx_label = ttk.Label(self.master, text=f"{i+1}")
      idx_label.grid(row=9+i, column=0, sticky=tk.E, padx=5)
      self.progress_bars_idx.append(idx_label)

      # Create progress bar
      progress_bar = CustomProgressBar(self.master, width=1202, height=20)
      progress_bar.grid(row=9 + i, column=1)
      progress_bar.bind("<Button-3>", lambda event, pb=progress_bar: self.toggle_pause(pb))
      progress_bar.bind("<Double-1>", lambda event, pb=progress_bar: self.confirm_and_kill_process(pb))
      self.progress_bars.append(progress_bar)

    # Create overall (total) progress bar
    ttk.Label(self.master, text="Overall progress:").grid(row=8, column=0, sticky=tk.W, padx=5)
    self.total_progress = CustomProgressBar(self.master, use_bold_font=True, width=1202, height=25)
    self.total_progress.grid(row=8, column=1, pady=10)  # Place it above progress bars for processed files
    self.total_progress.set_progress(0)
    self.total_progress.set_display_text("0%  0/0")

    self.run_button.config(state=tk.DISABLED)
    for progress_bar in self.progress_bars:
      progress_bar.set_progress(0)
    self.total_progress.set_display_text("0%  0/0")
    self.total_progress.set_progress(0)

    self.start_time = time.time()
    msg = "Starting processing..."
    self.update_status(msg)
    self.master.update_idletasks()
    logging.info(msg)
    self.start_process_files_threads()


  #############################################################################
  def update_status(self, message, replace=False):
    """Updates the status text area."""
    self.status_text.config(state=tk.NORMAL)  # Enable editing
    if replace:
      self.status_text.delete(1.0, tk.END)
    self.status_text.insert(tk.END, message + "\n")
    self.status_text.see(tk.END)
    self.status_text.config(state=tk.DISABLED)  # Disable editing
#    self.master.update_idletasks()


  #############################################################################
  def finish_processing(self, calc_time=True):
    """Handles processing completion."""
    if self.processing_complete:
      return
    self.processing_complete = True
    #
    processing_time_str = ""
    if (calc_time == True):
      end_time = time.time()
      processing_time = end_time - self.start_time
      # Convert time in seconds to "XX min YY sec" string, e.g. 95 sec = "1 min 35 sec"
      if processing_time < 60:
        processing_time_str += f"{processing_time:.2f} sec"
      else:
        processing_time_str += f"{int(processing_time/60)} min {int(processing_time%60)} sec"
    else:
      processing_time = 0

    # Example msg: "3 Files Total: 1 processed, 1 Skipped, 1 Error. Compression ratio  3.95"
    # Add Total and Processed files
    msg = f"{self.total_files} Files Total: {self.processed_files} Processed"
    # Add non-zero Skipped files
    if self.skipped_files:
      msg += f", {self.skipped_files} Skipped"
    # Add non-zero Error files
    if self.error_files:
      msg += f", {self.error_files} Errors"
    # Add non-zero Cancelled files
    if self.cancelled_files:
      msg += f", {self.cancelled_files} Cancelled"
    # Add Processing time
    if (processing_time != 0) and (self.skipped_files < self.total_files):
      msg += f" in {processing_time_str}."
    # Add Compression Ratio
    self.count_dst_files_sz()
    total_dst_sz_mb = self.total_dst_sz / (1024 * 1024)  #self.total_dst_sz_kb / 1024
    total_src_sz_mb = self.total_src_sz / (1024 * 1024)
    if total_dst_sz_mb:
      msg += f" Compression ratio {(total_src_sz_mb / total_dst_sz_mb):.2f}."

    # Display message
    self.update_status("\n" + msg)
    logging.info(msg)
    self.run_button.config(state=tk.NORMAL)

    # 100%
    total_progress_message = f"100%  {self.processed_files+self.skipped_files+self.cancelled_files}/{self.total_files}"
    self.total_progress.set_progress(100)
    self.total_progress.set_display_text(total_progress_message)

    # Clear the threads list
    self.threads.clear()
    self.master.update_idletasks()


  #############################################################################
  def setup_logging(self, log_level='INFO'):
    """Sets up logging to a file."""
    log_file = DFLT_LOG_FILE

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
      self.tempo.set(DFLT_TEMPO)  # Reset to default if invalid


  #############################################################################
  def process_status_updates(self):
    """Processes status updates from the queue."""
    while True:
      try:
        message = self.status_update_queue.get(timeout=0.1)  # Short timeout to avoid blocking indefinitely
        if message is None: # Check for exit signal
          break
        self.update_status(message)
        self.status_update_queue.task_done()
      except queue.Empty:
        if self.is_shutting_down:  # Check shutdown flag
          break
        continue
      except Exception as e:
        logging.exception("Error in status update thread: %s", e)
        break


  #############################################################################
  def kill_active_processes(self):
    """Terminates all active FFMPEG processes."""
    with self.processes_lock:
      for pid, process in self.active_processes.items():
        try:
          p = psutil.Process(pid)
          if p.status() != psutil.STATUS_ZOMBIE:
            p.kill()  # Force kill
        except psutil.NoSuchProcess:
          logging.warning(f"Process with PID {pid} not found, might have already finished.")
        except Exception as e:
          logging.error(f"Error killing process {pid}: {e}")
      self.active_processes.clear()


  #############################################################################
  def confirm_and_kill_process(self, progress_bar):
    """Confirms and kills a process, then starts the next file."""

    with self.processes_lock:
      pid = self.progress_bar_to_pid.get(progress_bar)
      if not pid:
        return

      filename = progress_bar.filename_var.get()
      try:
        p = psutil.Process(pid)
        p.suspend()
        progress_bar.paused.set(True)
        progress_bar.draw_progress_bar()
        if messagebox.askyesno("Confirm Kill", f"Are you sure you want to kill the process for {filename}?"):
          try:
            p.kill()
            # Wait for the process to terminate to release file locks
            p.wait(timeout=3)
          except psutil.NoSuchProcess:
            # Process already terminated, which is fine.
            pass
          except psutil.TimeoutExpired:
            logging.warning(f"Process {pid} did not terminate within the timeout.")

          progress_bar.cancelled.set(True)
          progress_bar.draw_progress_bar()
          self.cancelled_files += 1
          msg = f"Cancelled processing {filename}"
          logging.info(msg)
          self.status_update_queue.put(msg)

          # Rename the partially processed file
          if progress_bar.relative_path:
            dst_file_path = os.path.join(self.dst_dir.get(), progress_bar.relative_path)
            if os.path.exists(dst_file_path):
              base, ext = os.path.splitext(dst_file_path)
              new_path = f"{base}_cancelled{ext}"
              try:
                os.rename(dst_file_path, new_path)
                logging.info(f"Renamed partial file to {new_path}")
              except OSError as e:
                logging.error(f"Failed to rename partial file {dst_file_path}: {e}")

          # Remove the process from active tracking
          if pid in self.active_processes:
            del self.active_processes[pid]
          if progress_bar in self.progress_bar_to_pid:
            del self.progress_bar_to_pid[progress_bar]

          # Since a slot is now free, try to start a new task
          self.start_new_task_if_needed()
        else:
          p.resume()
          progress_bar.paused.set(False)

      except psutil.NoSuchProcess:
        logging.warning(f"Process with PID {pid} not found for cancellation.")
      except Exception as e:
        logging.error(f"Error killing process {pid}: {e}")


  #############################################################################
  def start_new_task_if_needed(self):
    """Checks if a new task can be started and starts one."""
    if not self.queue.empty() and self.active_threads < self.n_threads.get():
      # Find a free progress bar
      for i, pb in enumerate(self.progress_bars):
        if pb not in self.progress_bar_to_pid:
          thread = threading.Thread(target=self.worker, args=(i,), name=f"Worker-{i}")
          thread.daemon = True
          self.threads.append(thread)
          thread.start()
          self.active_threads += 1
          break


  #############################################################################
  def toggle_pause(self, progress_bar):
    """Toggles the paused state of a process."""
    with self.processes_lock:
      pid = self.progress_bar_to_pid.get(progress_bar)
      if not pid:
        return

      try:
        p = psutil.Process(pid)
        filename = progress_bar.filename_var.get()
        if p.status() == psutil.STATUS_STOPPED:
          p.resume()
          progress_bar.paused.set(False)
          msg = f"Resumed processing {filename}"
          logging.info(msg)
          self.status_update_queue.put(msg)
        else:
          p.suspend()
          progress_bar.paused.set(True)
          msg = f"Paused processing {filename}"
          logging.info(msg)
          self.status_update_queue.put(msg)
        progress_bar.draw_progress_bar()  # Redraw to reflect color change
      except psutil.NoSuchProcess:
        logging.warning(f"Process with PID {pid} not found for pause/resume.")
      except Exception as e:
        logging.error(f"Error toggling pause for process {pid}: {e}")


###############################################################################
if __name__ == "__main__":
  root = tk.Tk()
  app = VideoProcessor(root)
  root.mainloop()
