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


# Default values for the application
DEFAULT_FFMPEG_PATH = ""  # Change this if your ffmpeg path is different.
DEFAULT_TEMPO = 1.8
DEFAULT_N_THREADS = 4
# SIZE_TO_TIME_COEFFICIENT = 38.37 / 10249195  # seconds per byte
SIZE_TO_TIME_COEFFICIENT = 4.0E-07  # seconds per byte
DEFAULT_CONFIG_FILE = "tempo_config.ini"


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
  def set_filename(self, filename):
    """Sets the filename to be displayed and redraws the bar."""
    self.filename_var.set(filename)
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
    self.ffmpeg_path_default = DEFAULT_FFMPEG_PATH
    self.tempo_default = DEFAULT_TEMPO
    self.src_dir_default = ""
    self.dst_dir_default = ""
    self.n_threads_default = DEFAULT_N_THREADS
    self.overwrite_all_var = tk.BooleanVar()
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
#    self.overwrite_all_var.set(self.config['DEFAULT'].get('overwrite_all_var', self.overwrite_all_var))

    self.progress_bars = []
    self.active_threads = 0
    self.total_files = 0
    self.processed_files = 0
    self.error_files = 0
    self.status_text = None
    self.start_time = None
    self.overwrite_all = False
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

    self.setup_logging('INFO')  # or 'DEBUG' for more detailed logging
    logging.info("MP3Processor initialized")

    self.status_update_queue = queue.Queue()  # Queue for status updates
    self.status_update_thread = threading.Thread(target=self.process_status_updates)
    self.status_update_thread.start()  # Start the thread for processing status updates


  #############################################################################
  def load_config(self):
    """Loads application configuration from tempo_config.ini."""
    config_file_read = self.config.read(DEFAULT_CONFIG_FILE)
    if not config_file_read:
      logging.info("Warning: Config file not found or corrupted. Using defaults.")
      self.config['DEFAULT'] = {
        'ffmpeg_path': DEFAULT_FFMPEG_PATH,
        'tempo': str(DEFAULT_TEMPO),
        'src_dir': '',
        'dst_dir': '',
        'n_threads': str(DEFAULT_N_THREADS),
        'overwrite_all': 'false',  # Default no overwrite
        'use_compression': 'false',  # Default no compression
      }
    else:
      try:
        self.config['DEFAULT']['tempo'] = self.config['DEFAULT']['tempo'].split(';')[0].strip()
        # Load overwrite setting
        overwrite_setting = self.config['DEFAULT'].get('overwrite_all', 'false').lower()
        self.overwrite_all_var.set(overwrite_setting == 'true')
        # Load compression setting
        compression_setting = self.config['DEFAULT'].get('use_compression', 'false').lower()
        self.use_compression_var.set(compression_setting == 'true')

      except (KeyError, IndexError):
        messagebox.showwarning("Config Error",
                     "Tempo value missing or malformed in config file. Using default.")
        self.config['DEFAULT']['tempo'] = str(DEFAULT_TEMPO)


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
    self.config['DEFAULT']['overwrite_all'] = str(self.overwrite_all_var.get()).lower()  # Store overwrite setting
    self.config['DEFAULT']['use_compression'] = str(self.use_compression_var.get()).lower()  # Store compression setting
    try:
      with open(DEFAULT_CONFIG_FILE, 'w') as configfile:
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

    # Overwrite Checkbox
    ttk.Checkbutton(self.master, text="Overwrite all", variable=self.overwrite_all_var).grid(row=4, column=0, sticky=tk.W, padx=5)

    # Compression Checkbox
    ttk.Checkbutton(self.master, text="Use compression", variable=self.use_compression_var).grid(row=4, column=1, sticky=tk.W, padx=5)

    # Run button
    self.run_button = tk.Button(self.master, text="Run", command=self.start_processing, state=tk.NORMAL, height=2, width=20)
    self.run_button.grid(row=5, column=1, pady=10)  # Added pady for vertical space

    # Create a frame to hold the status_text and scrollbar
    status_frame = ttk.Frame(self.master)
    status_frame.grid(row=6, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)

    # Create the status_text widget
    self.status_text = tk.Text(status_frame, height=10, width=165, wrap=tk.WORD, state=tk.DISABLED)
    self.status_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Create the scrollbar
    scrollbar = ttk.Scrollbar(status_frame, orient=tk.VERTICAL, command=self.status_text.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # Configure the status_text to use the scrollbar
    self.status_text.config(yscrollcommand=scrollbar.set)


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
  def process_file(self, src_file_path, relative_path, progress_bar):
    """Processes a single MP3 file, handling potential overwrites."""
    if relative_path in self.processed_files_set:
      return  # Skip if already processed

    self.processed_files_set.add(relative_path)
    try:
      dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
      dst_fname = dst_file_path.split("\\")[-1]

      os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)

      # Handle Overwrite logic
      self.overwrite_all = self.overwrite_all_var.get()
      if os.path.exists(dst_file_path):
        if self.overwrite_all:  # Overwrite existing
          self.status_update_queue.put(f"Overwriting: {dst_fname}")  # Use queue for status updates
          logging.debug(f"Overwriting: {dst_file_path}")
        else:  # Rename instead of overwriting
          base, ext = os.path.splitext(relative_path)
          i = 1
          while os.path.exists(os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")):
            i += 1
          dst_file_path = os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")
          dst_fname = dst_file_path.split("\\")[-1]
          self.status_update_queue.put(f"Renaming: {dst_fname}")  # Use queue for status updates
          logging.debug(f"Renaming: {dst_file_path}")
      else:  # Normal output (no overwrite)
        self.status_update_queue.put(f"Processing: {dst_fname}")  # Use queue for status updates
        logging.debug(f"Processing: {dst_file_path}")

      # %ffmpeg% -i <ifile.mp3> -filter:a atempo=1.8 -vn <ofile.mp3> -y -nostats
      ffmpeg_command = [
        self.ffmpeg_path.get(),
        "-i", src_file_path,
        "-filter:a", f"atempo={self.tempo.get()}",  # audio filter
        "-vn",  # Disable video stream
        dst_file_path,
        "-y",  # Force overwrite output file
        "-nostats",  # Suppress extra logging
      ]

      # Use compression if enabled
      self.use_compression = self.use_compression_var.get()
      if self.use_compression:
        ffmpeg_compression_params = [
          "-codec:a", "libmp3lame",  # LAME (Lame Ain't an MP3 Encoder) MP3 encoder wrapper
          "-q:a", "7",  # quality setting for VBR
          "-ar", "22050"  # sample rate
        ]
        # insert compression params (after src_fname, before atempo filter arguments):
        # %ffmpeg% -i <ifile.mp3> -codec:a libmp3lame -q:a 7 -ar 22050 -filter:a atempo=1.8 -vn <ofile.mp3> -y -nostats
        ffmpeg_command[3:3] = ffmpeg_compression_params

      # Debug log the command
      logging.debug(f"FFMPEG command: {' '.join(ffmpeg_command)}")

      file_size = os.path.getsize(src_file_path)
      expected_duration = file_size * SIZE_TO_TIME_COEFFICIENT
      start_time = time.time()
      process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

      # Create a separate thread to monitor the process and update the progress bar
      progress_thread = threading.Thread(target=self.monitor_process,
                                        args=(process, expected_duration, progress_bar, dst_fname))
      progress_thread.start()

      stdout, stderr = process.communicate()
      end_time = time.time()
      logging.info(f"Processed file {src_file_path}")
      progress_thread.join()  # Wait for the progress thread to finish.
      progress_bar.set_progress(100)  # Ensure the progress bar reaches 100%
      self.master.update_idletasks()

    except FileNotFoundError:
      logging.error(f"FFMPEG not found or invalid path: {self.ffmpeg_path.get()}")
      self.status_update_queue.put(f"Error: FFMPEG not found for {relative_path}")  # Use queue for status updates
      self.error_files += 1
    except subprocess.CalledProcessError as e:
      logging.error(f"ffmpeg error processing {src_file_path}: return code {e.returncode}, output: {e.stderr.decode()}")
      self.status_update_queue.put(f"Error processing: {relative_path}")  # Use queue for status updates
      self.error_files += 1
    except Exception as e:
      logging.exception(f"An unexpected error occurred processing {src_file_path}: {e}")
      self.status_update_queue.put(f"Error: Unexpected issue with {relative_path}")  # Use queue for status updates
      self.error_files += 1
    finally:
      self.processed_files += 1
      if self.processed_files == self.total_files:
        self.master.after(100, self.finish_processing)


  #############################################################################
  def monitor_process(self, process, expected_duration, progress_bar, filename):
    """Monitors the FFMPEG process and updates the progress bar."""
    start_time = time.time()
    progress_bar.set_filename(filename)
    while process.poll() is None:
      elapsed_time = time.time() - start_time
      progress = min(100, (elapsed_time / expected_duration) * 100)
      progress_bar.set_progress(progress)
      self.master.update_idletasks()
      time.sleep(0.1)
    progress_bar.set_progress(100)  # Ensure the progress bar reaches 100%
    self.master.update_idletasks()


  #############################################################################
  def process_files(self):
    """Processes all MP3 files in the source directory using multiple threads."""
    src_dir = self.src_dir.get()
    self.total_files = 0
    self.queue = queue.Queue()
    self.processed_files_set.clear()

    for root, _, files in os.walk(src_dir):
      for file in files:
        if file.lower().endswith(".mp3"):
          full_path = os.path.join(root, file)
          relative_path = os.path.relpath(full_path, src_dir)
          self.queue.put((full_path, relative_path))
          self.total_files += 1

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
        self.process_file(file_path, relative_path, self.progress_bars[thread_index])
        self.queue.task_done()
      except queue.Empty:
        break
      except Exception as e:
        self.update_status(f"Error in thread {thread_index + 1}: {e}")
        logging.exception(f"Error in thread {thread_index + 1}")
        break
    self.active_threads -= 1
    if self.active_threads == 0:
      self.master.after(100, self.finish_processing)


  #############################################################################
  def on_closing(self):
    """Handles window closing event, saving configuration."""
    self.save_config()
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

    # Create n_threads CustomProgressBar dynamically
    self.progress_bars = []
    for i in range(self.n_threads.get()):
      progress_bar = CustomProgressBar(self.master, width=1202, height=20)
      progress_bar.grid(row=7 + i, column=1)
      self.progress_bars.append(progress_bar)

    self.run_button.config(state=tk.DISABLED)
    for progress_bar in self.progress_bars:
      progress_bar.set_progress(0)
    self.active_threads = 0
    self.processed_files = 0
    self.total_files = 0
    self.start_time = time.time()
    self.processing_complete = False
    self.processed_files_set.clear()
    self.status_text.config(state=tk.NORMAL)
    self.status_text.delete(1.0, tk.END)
    self.status_text.config(state=tk.DISABLED)
    self.update_status("Starting processing...")

    # Log the start of processing
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
        msg = f"\n{self.processed_files} files processed in {processing_time:.2f} seconds"
        self.update_status(msg)
        logging.info(msg)
      else:
        msg = f"\nProcessed / Total (Erorrs): {self.processed_files} / {self.total_files} ({self.error_files}) files in {processing_time:.2f} seconds"
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
        message = self.status_update_queue.get(timeout=1)  # Wait for a message
        self.update_status(message)  # Update the status text
        self.status_update_queue.task_done()  # Mark the task as done
      except queue.Empty:
        continue  # Continue if no messages are available


###############################################################################
if __name__ == "__main__":
  root = tk.Tk()
  app = MP3Processor(root)
  root.mainloop()

