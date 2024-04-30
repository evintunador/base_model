import torch
from torch import nn

###########################################################
########### DEBUGGING / DEMONSTRATION #####################
###########################################################
import functools
import inspect

# using this is way cleaner than cluttering up our code with print statements
def log_io(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        # Check if logging is enabled globally and for the specific function
        if not self.logging_enabled or func.__name__ in self.disabled_logging_functions:
            return func(self, *args, **kwargs)
        #if not self.logging_enabled:
            #return func(self, *args, **kwargs)

        def log_item(item, name, level=0, is_root=False):
            indent = "    " * level
            if isinstance(item, torch.Tensor):
                print(f"{indent}Tensor '{name}' shape: {item.shape}")
            elif isinstance(item, tuple):
                if is_root and level == 0:
                    # Root level tuple, don't print it as a tuple unless it's a "true" tuple
                    for idx, sub_item in enumerate(item):
                        log_item(sub_item, f"{name}[{idx}]", level)
                else:
                    print(f"{indent}Tuple '{name}':")
                    for idx, sub_item in enumerate(item):
                        log_item(sub_item, f"{name}[{idx}]", level + 1)
            elif isinstance(item, int):
                print(f"{indent}Integer '{name}': Value={item}")
            elif isinstance(item, float):
                print(f"{indent}Float '{name}': Value={item}")
            else:
                print(f"{indent}Other-type '{name}': Type={type(item).__name__}, Value={item}")

        print(f"\n{'='*10}Entering {self.__class__.__name__}.{func.__name__}{'='*10}")
        print("Inputs:")
        arg_names = inspect.getfullargspec(func).args[1:]  # Excluding 'self'
        arg_values = args + tuple(kwargs.values())
        for name, value in zip(arg_names, arg_values):
            log_item(value, name)

        result = func(self, *args, **kwargs)
        print("\nOutputs:")
        if isinstance(result, tuple):
            log_item(result, "output", is_root=True)
        else:
            log_item(result, "output")

        print(f"{'='*10}Exiting {self.__class__.__name__}.{func.__name__}{'='*10}")
        return result
    return wrapper

class LoggingModule(nn.Module):
    def __init__(self):
        super().__init__()
        self.logging_enabled = False
        self.disabled_logging_functions = set()

    def enable_logging(self):
        self.logging_enabled = True

    def disable_logging(self):
        self.logging_enabled = False

    def disable_function_logging(self, func_name):
        self.disabled_logging_functions.add(func_name)

    def enable_function_logging(self, func_name):
        self.disabled_logging_functions.discard(func_name)

###########################################################
################ LOADING DATA #############################
###########################################################
from torch.utils.data import Dataset, DataLoader
from datasets import load_dataset

class TinyStoriesDataset(Dataset):
    def __init__(self, split):
        # Load the dataset
        self.dataset = load_dataset("noanabeshima/TinyStoriesV2", split=split)
        
    def __len__(self):
        # Return the size of the dataset
        return len(self.dataset)
    
    def __getitem__(self, idx):
        # Fetch one item from the dataset
        return self.dataset[idx]['text']

def get_data_loader(batch_size=32, shuffle=True, split='train', num_workers=0):
    # Create the dataset
    dataset = TinyStoriesDataset(split)
    # Create the DataLoader
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers)

def torcherize_batch(tokenizer, batch, max_seq_len, device):
    b = torch.zeros(len(batch), max_seq_len+1)
    for i, s in enumerate(batch):
        b[i] = torch.tensor(
            tokenizer.encode(s, bos=True, eos=True, pad=max_seq_len+1), 
            device=device
        )
    x, y = b[:,:max_seq_len], b[:, 1:]
    return x.to(torch.long), y.to(torch.long)
    
###########################################################
############# SAVE / LOAD MODELS ##########################
###########################################################
import os
import json
from dataclasses import asdict
import time
import csv

def save_model(model, cfg, tcfg, log_data = None, checkpoint = False):
    if checkpoint == False:
        path = f'models/{tcfg.model_name}'
    else: 
        path = f'models/{tcfg.model_name}/checkpoint-{time.strftime("%Y-%m-%d|%H-%M-%S")}'
    os.makedirs(path, exist_ok=True)
    
    if log_data is not None:
        # Save training data to CSV
        with open(f'{path}/log_data.csv', 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Step', 
                'Learning Rate', 
                'Train Loss', 
                'Validation Loss', 
                'Perplexity', 
                'Time Elapsed'
            ])
            writer.writerows(log_data)
    
    # saving model
    torch.save(model.state_dict(), f'{path}/model.pth')
    
    # saving configs
    cfg_dict = asdict(cfg)
    with open(f'{path}/model_config.json', 'w') as f:
        json.dump(cfg_dict, f)
    tcfg_dict = asdict(tcfg)
    with open(f'{path}/train_config.json', 'w') as f:
        json.dump(tcfg_dict, f)

def load_model(
    name: str, 
    device: str = 'cuda' if torch.cuda.is_available() else 'cpu',
):
    from config import ModelConfig
    from tokenizer import get_tokenizer
    from model import customGPT

    model_name = f'models/{name}'

    # Deserialize the JSON file back to a dictionary
    with open(f'{model_name}/model_config.json', 'r') as f:
        config_dict = json.load(f)
    
    # Convert the dictionary back to a Config object
    cfg = ModelConfig(**config_dict)
    cfg.device = device
    
    # tokenizer
    vocab_size = cfg.vocab_len - 3
    tokenizer = get_tokenizer(vocab_size) 
    
    # Initialize a blank model
    model = customGPT(cfg).to(cfg.device) 
    
    # Load the saved state dictionary
    path = f'{model_name}/model.pth'
    model.load_state_dict(torch.load(path)) 
    
    print(cfg, '\n\n', sum(p.numel() for p in model.parameters())/1e3, 'K parameters')

    return model, tokenizer, cfg

###########################################################
############# MODEL COMPARISONS ###########################
###########################################################
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

def plot_column_from_csv(models, x_column, y_column, log_x=False, log_y=False, trim_percentage=0):
    """
    Reads multiple CSV files, extracts specific columns, and plots a graph comparing their values
    with options to use logarithmic scales on the axes and trim initial data.

    Parameters:
    - models: List of strings, where each string is a name of a model residing in models/
    - x_column: String, the name of the column to use as the x-axis.
    - y_column: String, the name of the column to use as the y-axis.
    - log_x: Boolean, whether to use a logarithmic scale on the x-axis (default is False).
    - log_y: Boolean, whether to use a logarithmic scale on the y-axis (default is False).
    - trim_percentage: Integer, percentage of the initial data to exclude from the plot (default is 0).
    """
    if not models or not x_column or not y_column:
        raise ValueError("Paths list and column names must be provided and not empty.")

    plt.figure(figsize=(10, 6))
    
    for model in models:
        path = f'models/{model}/log_data.csv'
        try:
            data = pd.read_csv(path)
            if x_column not in data.columns or y_column not in data.columns:
                raise ValueError(f"Columns {x_column} or {y_column} not found in {path}")

            # Calculate the index to start slicing from
            start_index = int(len(data) * (trim_percentage / 100))

            # Slice data from the calculated start index
            data = data.iloc[start_index:]
            plt.plot(data[x_column], data[y_column], label=f'{model}')
        except Exception as e:
            print(f"Failed to process {path}: {str(e)}")
    
    if log_x:
        plt.xscale('log')
    if log_y:
        plt.yscale('log')

    plt.title(f'{y_column} Over Training {x_column}s')
    plt.xlabel(x_column + (' (log scale)' if log_x else ''))
    plt.ylabel(y_column + (' (log scale)' if log_y else ''))
    plt.legend(title="Model")
    plt.grid(True)
    plt.show()