import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
import configparser
import os
import subprocess
import queue
import time
from tkinter import messagebox
import logging

# Configure logging
logging.basicConfig(filename='tempo_processing.log', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Default values for the application
DEFAULT_SOX_PATH = "sox"  # Change this if your sox path is different.
DEFAULT_TEMPO = 1.8
DEFAULT_N_THREADS = 4
SIZE_TO_TIME_COEFFICIENT = 38.37 / 10249195  # seconds per byte
DEFAULT_CONFIG_FILE = "tempo_config.ini"


class MP3Processor:
    # Main class for the MP3 tempo changer application
    def __init__(self, master):
        self.master = master
        master.title("MP3 Tempo Changer")

        # Default values (used only if config file loading fails)
        self.sox_path_default = DEFAULT_SOX_PATH
        self.tempo_default = DEFAULT_TEMPO
        self.src_dir_default = ""
        self.dst_dir_default = ""
        self.n_threads_default = DEFAULT_N_THREADS

        # Pre-define GUI element variables (to avoid linter warnings)
        self.run_button = None

        # Load application configuration
        self.config = configparser.ConfigParser()
        self.load_config()

        # Initialize GUI variables as empty
        self.sox_path = tk.StringVar()
        self.tempo = tk.DoubleVar()
        self.src_dir = tk.StringVar()
        self.dst_dir = tk.StringVar()
        self.n_threads = tk.IntVar()

        # Set the values using the loaded configuration or defaults
        self.sox_path.set(self.config['DEFAULT'].get('sox_path', self.sox_path_default))
        self.tempo.set(float(self.config['DEFAULT'].get('tempo', str(self.tempo_default))))
        self.src_dir.set(self.config['DEFAULT'].get('src_dir', self.src_dir_default))
        self.dst_dir.set(self.config['DEFAULT'].get('dst_dir', self.dst_dir_default))
        self.n_threads.set(int(self.config['DEFAULT'].get('n_threads', str(self.n_threads_default))))

        print("n_threads: ", self.n_threads.get())

        self.progress_vars = []

        # Create GUI elements
        self.create_widgets()
        # Initialize threading components
        self.queue = queue.Queue()
        self.processing_complete = False

        # Bind the save_config method to the window close event.
        self.master.protocol("WM_DELETE_WINDOW", self.on_closing)


    # Load configuration from config.ini file
    def load_config(self):
        config_file_read = self.config.read(DEFAULT_CONFIG_FILE)
        if not config_file_read:
            print("Warning: Config file not found or corrupted. Using defaults.")
            self.config['DEFAULT'] = {
                'sox_path': DEFAULT_SOX_PATH,
                'tempo': str(DEFAULT_TEMPO),
                'src_dir': '',
                'dst_dir': '',
                'n_threads': str(DEFAULT_N_THREADS)
            }
        else:
            try:
                self.config['DEFAULT']['tempo'] = self.config['DEFAULT']['tempo'].split(';')[0].strip()
            except (KeyError, IndexError):
                messagebox.showwarning("Config Error",
                                       "Tempo value missing or malformed in config file. Using default.")
                self.config['DEFAULT']['tempo'] = str(DEFAULT_TEMPO)


    # Save application configuration to config.ini file
    def save_config(self):
        self.config['DEFAULT']['sox_path'] = self.sox_path.get()
        self.config['DEFAULT']['tempo'] = str(self.tempo.get())
        self.config['DEFAULT']['src_dir'] = self.src_dir.get()
        self.config['DEFAULT']['dst_dir'] = self.dst_dir.get()
        self.config['DEFAULT']['n_threads'] = str(self.n_threads.get())
        try:
            with open(DEFAULT_CONFIG_FILE, 'w') as configfile:
                self.config.write(configfile)
        except Exception as e:
            messagebox.showerror("Error", f"Could not save config file: {e}")


    # Create and arrange GUI elements
    def create_widgets(self):
        ttk.Label(self.master, text="Path To SoX:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(self.master, textvariable=self.sox_path, width=50).grid(row=0, column=1)

        ttk.Label(self.master, text="Tempo:").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(self.master, textvariable=self.tempo, width=5).grid(row=1, column=1)

        ttk.Label(self.master, text="Source Directory Path:").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(self.master, textvariable=self.src_dir, width=50).grid(row=2, column=1)
        ttk.Button(self.master, text="SrcDir", command=self.browse_src_dir).grid(row=2, column=2)

        ttk.Label(self.master, text="Destination Directory Path:").grid(row=3, column=0, sticky=tk.W)
        ttk.Entry(self.master, textvariable=self.dst_dir, width=50).grid(row=3, column=1)
        ttk.Button(self.master, text="DstDir", command=self.browse_dst_dir).grid(row=3, column=2)

        ttk.Label(self.master, text="Number of Threads:").grid(row=4, column=0, sticky=tk.W)
        ttk.Entry(self.master, textvariable=self.n_threads, width=5).grid(row=4, column=1)

        ttk.Label(self.master, text="Size-to-Time Coefficient:").grid(row=5, column=0, sticky=tk.W)
        ttk.Label(self.master, text=f"{SIZE_TO_TIME_COEFFICIENT:.6f}").grid(row=5, column=1)

        self.run_button = ttk.Button(self.master, text="Run", command=self.start_processing, state=tk.NORMAL)
        self.run_button.grid(row=6, column=1)

        self.progress_bars = []
        for i in range(DEFAULT_N_THREADS):
            progress_var = tk.DoubleVar()
            self.progress_vars.append(progress_var)
            progress_bar = ttk.Progressbar(self.master, variable=progress_var, maximum=100)
            progress_bar.grid(row=7 + i, column=1)
            self.progress_bars.append(progress_bar)

    # Opens a directory selection dialog for the source directory
    def browse_src_dir(self):
        directory = filedialog.askdirectory()
        self.src_dir.set(directory)

    # Opens a directory selection dialog for the destination directory
    def browse_dst_dir(self):
        directory = filedialog.askdirectory()
        self.dst_dir.set(directory)

    # Processes a single MP3 file, handling file overwriting
    def process_file(self, file_path, relative_path):
        try:
            logging.debug(f"Starting processing: {file_path}")
            print(f"Starting processing: {file_path}")
            dst_file_path = os.path.join(self.dst_dir.get(), relative_path)
            # Create the directory if it doesn't exist
            os.makedirs(os.path.dirname(dst_file_path), exist_ok=True)  #Added this line
            logging.debug(f"Destination file path: {dst_file_path}")
            i = 1
            base, ext = os.path.splitext(dst_file_path)
            while os.path.exists(dst_file_path):
                if self.overwrite_all:
                    break
                overwrite = messagebox.askyesnocancel(
                    "File Exists",
                    f"The file '{os.path.basename(dst_file_path)}' already exists. Overwrite?",
                    default=messagebox.CANCEL
                )
                if overwrite is None:
                    return
                elif overwrite:
                    break
                else:
                    dst_file_path = f"{base}({i}){ext}"
                    i += 1

            sox_command = [
                self.sox_path.get(),
                file_path,
                dst_file_path,
                "tempo", str(self.tempo.get())
            ]
            logging.debug(f"SoX command: {' '.join(sox_command)}")

            try:
                file_size = os.path.getsize(file_path)
                start_time = time.time()
                process = subprocess.Popen(sox_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = process.communicate(timeout=20)
                end_time = time.time()
                actual_size_to_time = (end_time - start_time) / file_size
                logging.info(
                    f"File {file_path} processed. Actual Size-to-Time: {actual_size_to_time:.6f}")
                print(
                    f"File {file_path} processed. Actual Size-to-Time: {actual_size_to_time:.6f}")

                logging.debug(f"SoX stdout: {stdout.decode()}")
                if stderr:
                    logging.error(f"SoX stderr: {stderr.decode()}")

            except FileNotFoundError:
                logging.error(f"SoX not found or invalid path: {self.sox_path.get()}")
                print(f"SoX not found or invalid path: {self.sox_path.get()}")
            except subprocess.CalledProcessError as e:
                logging.error(
                    f"SoX error processing {file_path}: return code {e.returncode}, output: {e.stderr.decode()}")
                print(
                    f"SoX error processing {file_path}: return code {e.returncode}, output: {e.stderr.decode()}")
            except subprocess.TimeoutExpired:
                logging.error(f"SoX process timed out for {file_path}")
                print(f"SoX process timed out for {file_path}")
            except Exception as e:
                logging.exception(f"An unexpected error occurred processing {file_path}: {e}")
                print(f"An unexpected error occurred processing {file_path}: {e}")

        except FileNotFoundError:
            logging.error(f"Input file not found: {file_path}")
            print(f"Input file not found: {file_path}")
        except Exception as e:
            logging.exception(f"An unexpected error occurred before SoX execution for {file_path}: {e}")
            print(f"An unexpected error occurred before SoX execution for {file_path}: {e}")


    # Processes all MP3 files in the source directory using multiple threads
    def process_files(self):
        self.save_config()
        src_dir = self.src_dir.get()
        dst_dir = self.dst_dir.get()

        if not src_dir or not dst_dir or not self.sox_path.get():
            print("Error: Please specify all parameters.")
            return

        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)

        self.overwrite_all = False
        self.run_button.config(state=tk.DISABLED)

        for root, _, files in os.walk(src_dir):  # os.walk traverses subdirectories
            for file in files:
                if file.lower().endswith(".mp3"):
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, src_dir)  # Get relative path
                    self.queue.put((full_path, relative_path))  # add file to the queue

        print(f"Number of files to process: {self.queue.qsize()}")
        self.process_next_file()


    def process_next_file(self):
        try:
            file_path, relative_path = self.queue.get_nowait()
            print(f"Processing: {file_path}")
            self.process_file(file_path, relative_path)
            self.master.after(0, self.process_next_file)  # Process next file in the queue
        except queue.Empty:
            print("Processing complete.")
            self.run_button.config(state=tk.NORMAL)


    def on_closing(self):
        self.save_config()
        self.master.destroy()


    def start_processing(self):
        self.save_config()
        self.run_button.config(state=tk.DISABLED)
        self.process_files()


root = tk.Tk()
mp3_processor = MP3Processor(root)
root.mainloop()