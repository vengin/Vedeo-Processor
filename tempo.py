import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from tkinter import scrolledtext
import configparser
import os
import subprocess
import queue
import time
from tkinter import messagebox
import logging
from datetime import datetime
import threading

# Configure logging
logging.basicConfig(filename='tempo_log.txt', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Default values for the application
DEFAULT_FFMPEG_PATH = ""  # Change this if your ffmpeg path is different.
DEFAULT_TEMPO = 1.8
DEFAULT_N_THREADS = 4
# SIZE_TO_TIME_COEFFICIENT = 38.37 / 10249195  # seconds per byte
SIZE_TO_TIME_COEFFICIENT = 2.7E-07  # seconds per byte
DEFAULT_CONFIG_FILE = "tempo_config.ini"


#############################################################################
class CustomProgressBar(tk.Canvas):
  def __init__(self, master, *args, **kwargs):
    super().__init__(master, *args, **kwargs)
    self.progress_var = tk.DoubleVar()
    self.filename_var = tk.StringVar()
    self.progress_rect = None
    self.text_id = None
    self.outline_rect = None
    self.draw_progress_bar()

  def draw_progress_bar(self):
    self.delete("all")
    width = self.winfo_width()
    height = self.winfo_height()
    progress = self.progress_var.get()
    fill_width = (width - 4) * (progress / 100)

    # Draw the outline rectangle
    self.outline_rect = self.create_rectangle(2, 2, width - 2, height - 2, outline="black")

    # Draw the filled progress rectangle
    self.progress_rect = self.create_rectangle(2, 2, fill_width + 2, height - 2, fill="#C0C0C0")

    # Draw the filename text
    self.text_id = self.create_text(width / 2, height / 2, text=self.filename_var.get(), fill="black")

  def set_progress(self, value):
    self.progress_var.set(value)
    self.draw_progress_bar()

  def set_filename(self, filename):
    self.filename_var.set(filename)
    self.draw_progress_bar()


#############################################################################
class MP3Processor:
  # Main class for the MP3 tempo changer application
  def __init__(self, master):
    self.master = master
    master.title("MP3 Tempo Changer")

    # Default values (used only if config file loading fails)
    self.ffmpeg_path_default = DEFAULT_FFMPEG_PATH
    self.tempo_default = DEFAULT_TEMPO
    self.src_dir_default = ""
    self.dst_dir_default = ""
    self.n_threads_default = 4  # or whatever default you prefer
    self.overwrite_all_var = tk.BooleanVar()

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
#    self.overwrite_all_var = tk.BooleanVar() # Added overwrite variable

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
    self.status_text = None
    self.processed_files = 0
    self.start_time = None
    self.overwrite_all = False
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

    self.setup_logging()


  #############################################################################
  # Load configuration from config.ini file
  def load_config(self):
    config_file_read = self.config.read(DEFAULT_CONFIG_FILE)
    if not config_file_read:
      print("Warning: Config file not found or corrupted. Using defaults.")
      self.config['DEFAULT'] = {
        'ffmpeg_path': DEFAULT_FFMPEG_PATH,
        'tempo': str(DEFAULT_TEMPO),
        'src_dir': '',
        'dst_dir': '',
        'n_threads': str(DEFAULT_N_THREADS),
        'overwrite_all': 'false' # Added default for overwrite
      }
    else:
      try:
        self.config['DEFAULT']['tempo'] = self.config['DEFAULT']['tempo'].split(';')[0].strip()
        #Load overwrite setting
        overwrite_setting = self.config['DEFAULT'].get('overwrite_all', 'false').lower()
        self.overwrite_all_var.set(overwrite_setting == 'true')

      except (KeyError, IndexError):
        messagebox.showwarning("Config Error",
                     "Tempo value missing or malformed in config file. Using default.")
        self.config['DEFAULT']['tempo'] = str(DEFAULT_TEMPO)


  #############################################################################
  # Save application configuration to config.ini file
  def save_config(self):
    if self.validate_tempo():
      self.config['DEFAULT']['tempo'] = str(self.tempo.get())
    else:
      self.config['DEFAULT']['tempo'] = str(self.tempo_default)

    self.config['DEFAULT']['n_threads'] = str(self.n_threads.get())

    self.config['DEFAULT']['ffmpeg_path'] = self.ffmpeg_path.get()
    self.config['DEFAULT']['src_dir'] = self.src_dir.get()
    self.config['DEFAULT']['dst_dir'] = self.dst_dir.get()
    self.config['DEFAULT']['overwrite_all'] = str(self.overwrite_all_var.get()).lower() # Store overwrite setting
    try:
      with open(DEFAULT_CONFIG_FILE, 'w') as configfile:
        self.config.write(configfile)
    except Exception as e:
      messagebox.showerror("Error", f"Could not save config file: {e}")


  #############################################################################
  # Create and arrange GUI elements
  def create_widgets(self):
    ttk.Label(self.master, text="Tempo:").grid(row=0, column=0, sticky=tk.W, padx=5)
    tempo_entry = ttk.Entry(self.master, textvariable=self.tempo, width=5)
    tempo_entry.grid(row=0, column=1, sticky=tk.W)
    tempo_entry.bind('<FocusOut>', self.on_tempo_focusout)

    # Source Directory Path
    ttk.Button(self.master, text="SrcDir", command=self.browse_src_dir).grid(row=1, column=0)
    ttk.Entry(self.master, textvariable=self.src_dir, width=199).grid(row=1, column=1, sticky=tk.W)

    # Destination Directory Path
    ttk.Button(self.master, text="DstDir", command=self.browse_dst_dir).grid(row=2, column=0)
    ttk.Entry(self.master, textvariable=self.dst_dir, width=199).grid(row=2, column=1, sticky=tk.W)

    ttk.Label(self.master, text="Number of threads:").grid(row=3, column=0, sticky=tk.W, padx=5)
    thread_values = list(range(1, 17))  # Creates a list from 1 to 16
    self.n_threads_combo = ttk.Combobox(self.master, textvariable=self.n_threads, values=thread_values, width=3, state="readonly")
    self.n_threads_combo.grid(row=3, column=1, sticky=tk.W)
    self.n_threads_combo.bind('<<ComboboxSelected>>', self.on_n_threads_change)

    # Overwrite Checkbox
    ttk.Checkbutton(self.master, text="Overwrite all", variable=self.overwrite_all_var).grid(row=4, column=0, sticky=tk.W, padx=5)

    # Run button
    self.run_button = tk.Button(self.master, text="Run", command=self.start_processing, state=tk.NORMAL, height=2, width=20)
    self.run_button.grid(row=5, column=1, pady=10)  # Added pady for vertical space

    # Add this block before creating <n_threads> progress bars
    ttk.Label(self.master, text="Processing Status:").grid(row=6, column=0, sticky=tk.N, pady=5)
    self.status_text = tk.Text(self.master, wrap=tk.WORD, width=150, height=8)
    self.status_text.grid(row=6, column=1, padx=5, pady=5)
    self.status_text.config(state=tk.DISABLED)  # Initially disable the widget

  def on_n_threads_change(self, event):
    # This method can be used if you need to perform any action when the selection changes
    pass

  #############################################################################
  # Opens a directory selection dialog for the source directory
  def browse_src_dir(self):
    current_dir = self.src_dir.get()
    directory = filedialog.askdirectory(initialdir=current_dir if current_dir else None)
    if directory:  # Check if a directory was selected
        self.src_dir.set(os.path.normpath(directory))


  #############################################################################
  # Opens a directory selection dialog for the destination directory
  def browse_dst_dir(self):
    current_dir = self.dst_dir.get()
    directory = filedialog.askdirectory(initialdir=current_dir if current_dir else None)
    if directory:  # Check if a directory was selected
        self.dst_dir.set(os.path.normpath(directory))


  #############################################################################
  # Processes a single MP3 file, handling file overwriting
  def process_file(self, src_file_path, relative_path, progress_bar):
    if relative_path in self.processed_files_set:
      return  # Skip if already processed

    self.processed_files_set.add(relative_path)
    try:
      # Add this line to update the status when processing starts
      self.update_status(f"Processing: {relative_path}")

      logging.debug(f"Starting processing: {src_file_path}")
      dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
      os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)
      logging.debug(f"Destination file path: {dst_file_path}")

      #Handle Overwrite logic
      self.overwrite_all = self.overwrite_all_var.get()
      if os.path.exists(dst_file_path) and not self.overwrite_all:
        base, ext = os.path.splitext(relative_path)
        i = 1
        while os.path.exists(os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")):
          i += 1
        dst_file_path = os.path.join(self.dst_dir.get(), f"{base}({i}){ext}")
        logging.debug(f"Destination file path (after renaming): {dst_file_path}")

      # %ffmpeg% -i <ifile.mp3> -filter:a "atempo=1.8" -vn <ofile.mp3> -y -nostats
      tempo = self.tempo.get()
      ffmpeg_command = [
        self.ffmpeg_path.get(),
        "-i", src_file_path,
        "-filter:a", f"atempo={self.tempo.get()}",  # audio filter
        "-vn", # Disable video stream
        dst_file_path,
        "-y", # Force overwrite output file
        "-nostats",  # Suppress extra logging
      ]

      # Debug
      logging.debug(f"FFMPEG command: {' '.join(ffmpeg_command)}")

      try:
        file_size = os.path.getsize(src_file_path)
        expected_duration = file_size * SIZE_TO_TIME_COEFFICIENT
        start_time = time.time()
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        #print(subprocess.PIPE)

        # Create a separate thread to monitor the process and update the progress bar
        progress_thread = threading.Thread(target=self.monitor_process, args=(process, expected_duration, progress_bar, relative_path))
        progress_thread.start()

        stdout, stderr = process.communicate()
        end_time = time.time()
        logging.info(f"File {src_file_path} processed.")
        print(f"File {src_file_path} processed.")
        progress_thread.join()  # Wait for the progress thread to finish.
        progress_bar.set_progress(100)  # Ensure the progress bar reaches 100%
        self.master.update_idletasks()

      except FileNotFoundError:
        logging.error(f"FFMPEG not found or invalid path: {self.ffmpeg_path.get()}")
        print(f"FFMPEG not found or invalid path: {self.ffmpeg_path.get()}")
        self.update_status(f"Error: FFMPEG not found for {relative_path}")
      except subprocess.CalledProcessError as e:
        logging.error(
          f"Ffmpeg error processing {src_file_path}: return code {e.returncode}, output: {e.stderr.decode()}")
        print(
          f"Ffmpeg error processing {src_file_path}: return code {e.returncode}, output: {e.stderr.decode()}")
        self.update_status(f"Error processing: {relative_path}")
      except Exception as e:
        logging.exception(f"An unexpected error occurred processing {src_file_path}: {e}")
        print(f"An unexpected error occurred processing {src_file_path}: {e}")
        self.update_status(f"Error: Unexpected issue with {relative_path}")
    except FileNotFoundError:
      logging.error(f"Input file not found: {src_file_path}")
      print(f"Input file not found: {src_file_path}")
      self.update_status(f"Error: File not found - {relative_path}")
    except Exception as e:
      logging.exception(f"An unexpected error occurred before Ffmpeg execution for {src_file_path}: {e}")
      print(f"An unexpected error occurred before Ffmpeg execution for {src_file_path}: {e}")
      self.update_status(f"Error: Unexpected issue before processing {relative_path}")
    finally:
      self.processed_files += 1
      if self.processed_files == self.total_files:
        self.master.after(100, self.finish_processing)


  #############################################################################
  def monitor_process(self, process, expected_duration, progress_bar, filename):
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
  # Processes all MP3 files in the source directory using multiple threads
  def process_files(self):
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

    print(f"Number of files to process: {self.total_files}")
    self.start_threads()


  #############################################################################
  def start_threads(self):
    num_threads = min(self.n_threads.get(), self.total_files)
    self.active_threads = num_threads
    for i in range(num_threads):
      thread = threading.Thread(target=self.worker, args=(i,))
      self.threads.append(thread)
      thread.start()


  #############################################################################
  def worker(self, thread_index):
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
    self.save_config()
    self.master.destroy()


  #############################################################################
  def start_processing(self):
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
    self.status_text.config(state=tk.NORMAL)  # Enable editing
    self.status_text.insert(tk.END, message + "\n")
    self.status_text.see(tk.END)
    self.status_text.config(state=tk.DISABLED)  # Disable editing
    self.master.update_idletasks()


  #############################################################################
  def finish_processing(self):
    if not self.processing_complete:
      self.processing_complete = True
      end_time = time.time()
      processing_time = end_time - self.start_time
      self.update_status(f"\n{self.processed_files} files processed in {processing_time:.2f} seconds")
      self.run_button.config(state=tk.NORMAL)

      # Clear the threads list
      self.threads.clear()

      # # Set progress bars to 100%
      # for progress_bar in self.progress_bars:
      #   progress_bar.set_progress(100)

      self.master.update_idletasks()


  #############################################################################
  def setup_logging(self):
    log_file = 'tempo_log.txt'
    logging.basicConfig(filename=log_file, level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s')

    # Add separator and timestamp to the log file
    with open(log_file, 'a') as f:
      timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
      separator = f"\n\n==================== START OF LOG - {timestamp} ====================\n"
      f.write(separator)

  #############################################################################
  def validate_tempo(self):
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
    if not self.validate_tempo():
      self.tempo.set(self.tempo_default)  # Reset to default if invalid


###############################################################################
if __name__ == "__main__":
  root = tk.Tk()
  app = MP3Processor(root)
  root.mainloop()

