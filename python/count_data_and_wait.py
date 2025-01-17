#!/usr/bin/python3
# Shuffles npz selfplay data for training, choosing a window size based on a power law.
# Run 'python shuffle.py --help' for details on how the window size is chosen and how to use this script.
import sys
import os
import argparse
import traceback
import math
import time
import logging
import zipfile
import shutil
import psutil
import json
import hashlib
import datetime
import gc

import multiprocessing

import numpy as np

keys = [
    "binaryInputNCHWPacked",
    "globalInputNC",
    "policyTargetsNCMove",
    "globalTargetsNC",
    "scoreDistrN",
    "valueTargetsNCHW"
]

def is_temp_npz_like(filename):
    return "_" in filename


def get_numpy_npz_headers(filename):
    with zipfile.ZipFile(filename) as z:
        wasbad = False
        numrows = 0
        npzheaders = {}
        for subfilename in z.namelist():
            npyfile = z.open(subfilename)
            try:
                version = np.lib.format.read_magic(npyfile)
            except ValueError:
                wasbad = True
                print("WARNING: bad file, skipping it: %s (bad array %s)" % (filename,subfilename))
            else:
                (shape, is_fortran, dtype) = np.lib.format._read_array_header(npyfile,version)
                npzheaders[subfilename] = (shape, is_fortran, dtype)
        if wasbad:
            return None
        return npzheaders


def compute_num_rows(filename):
    try:
        npheaders = get_numpy_npz_headers(filename)
    except PermissionError:
        print("WARNING: No permissions for reading file: ", filename)
        return (filename,None)
    except zipfile.BadZipFile:
        print("WARNING: Bad zip file: ", filename)
        return (filename,None)
    if npheaders is None or len(npheaders) <= 0:
        print("WARNING: bad npz headers for file: ", filename)
        return (filename,None)

    if "binaryInputNCHWPacked" in npheaders:
        (shape, is_fortran, dtype) = npheaders["binaryInputNCHWPacked"]
    else:
        (shape, is_fortran, dtype) = npheaders["binaryInputNCHWPacked.npy"]
    num_rows = shape[0]
    return (filename,num_rows)


class TimeStuff(object):

    def __init__(self,taskstr):
        self.taskstr = taskstr

    def __enter__(self):
        #print("Beginning: %s" % self.taskstr, flush=True)
        self.t0 = time.time()

    def __exit__(self, exception_type, exception_val, trace):
        self.t1 = time.time()
        print("Finished: %s in %s seconds" % (self.taskstr, str(self.t1 - self.t0)), flush=True)
        return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False,formatter_class=argparse.RawTextHelpFormatter,description="""
    Shuffle data files!

    This shuffle script is designed for ongoing self-play training. It shuffles the most recent window of data among the data it's provided. It chooses the window size dynamically based on the total amount of data in the run so far, assuming that the directories provided contain all of the data for the run so far. If you don't actually have all of the data, e.g. you've archived or deleted the older data, or else want to compute the window size as if there were more data, use -add-to-data-rows.

    The window size is a power law based on the number of rows in the run N:
      WINDOWSIZE(N) = (N^EXPONENT - MIN_ROWS^EXPONENT) / (EXPONENT * MIN_ROWS^(EXPONENT-1)) * INITIAL_WINDOW_PER_ROW + MIN_ROWS

    given arguments:
      -taper-window-exponent EXPONENT \\
      -expand-window-per-row INITIAL_WINDOW_PER_ROW \\
      -min-rows MIN_ROWS  (default 250k)

    This may look a bit complex, but basically it is simply the power law N^EXPONENT with shifting and scaling such that:
    WINDOWSIZE(MIN_ROWS) = MIN_ROWS
    (dWINDOWSIZE/dN)(MIN_ROWS) = INITIAL_WINDOW_PER_ROW

    Reasonable arguments similar to those used for KataGo's main runs would be
      -taper-window-exponent 0.65 or 0.675 \\
      -expand-window-per-row 0.4 \\
      -min-rows 250000 (default)

    If you want to control the "scale" of the power law differently than the min rows, you can specify -taper-window-scale as well.
    There is also a bit of a hack to cap the number of random rows (rows generated by random play without a neural net), since random row generation at the start of a run can be very fast due to not hitting the GPU, and overpopulate the run.

    Additionally, NOT all of the shuffled window is output, only a random shuffled 20M rows will be kept. Adjust this using -keep-target-rows. The intention is that this script will be repeatedly run as new data comes in, such that well before train.py would need more than 20M rows, the data would have been shuffled again and a new random 20M rows chosen.

    If you are NOT doing ongoing self-play training, but simply want to shuffle an entire dataset (not just a window of it) and want to output all of it (not just 20M of it) then you can use arguments like:
      -taper-window-exponent 1.0 \\
      -expand-window-per-row 1.0 \\
      -keep-target-rows SOME_VERY_LARGE_NUMBER

    If you ARE doing ongoing self-play training, but want a fixed window size, then you can use arguments like:
      -min-rows YOUR_DESIRED_SIZE \\
      -taper-window-exponent 1.0 \\
      -expand-window-per-row 0.0
    """)
    parser.add_argument('dirs', metavar='DIR', nargs='+', help='Directories of training data files')

    required_args = parser.add_argument_group('required arguments')
    optional_args = parser.add_argument_group('optional arguments')
    optional_args.add_argument(
        '-h',
        '--help',
        action='help',
        default=argparse.SUPPRESS,
        help='show this help message and exit'
    )
    optional_args.add_argument('-min-rows', type=int, required=True, help='Minimum training rows to use, default 250k')
    optional_args.add_argument('-max-rows', type=int, required=False, help='Maximum training rows to use, default unbounded')
    required_args.add_argument('-expand-window-per-row', type=float, required=True, help='Beyond min rows, initially expand the window by this much every post-random data row')
    required_args.add_argument('-taper-window-exponent', type=float, required=True, help='Make the window size asymtotically grow as this power of the data rows')
    optional_args.add_argument('-taper-window-scale', type=float, required=False, help='The scale at which the power law applies, defaults to -min-rows')
    optional_args.add_argument('-add-to-data-rows', type=float, required=False, help='Compute the window size as if the number of data rows were this much larger/smaller')
    optional_args.add_argument('-add-to-window-size', type=float, required=False, help='DEPRECATED due to being misnamed name, use -add-to-data-rows')
    optional_args.add_argument('-min-new-rows', type=int, required=True, help='How many new data rows are required for one training')
    optional_args.add_argument('-window-factor', type=float, required=True, help='Switch window factor when there are too many new rows')
    optional_args.add_argument('-check-wait-seconds', type=int, required=True, help='How long to wait for next check')
    optional_args.add_argument('-summary-file', required=False, help='Summary json file for directory contents')
    required_args.add_argument('-record-file', required=True, help='Path to record last data rows number')
    
    required_args.add_argument('-num-processes', type=int, required=True, help='Number of multiprocessing processes for shuffling in parallel')
    
    
    optional_args.add_argument('-exclude', required=False, help='Text file with npzs to ignore, one per line')
    optional_args.add_argument('-exclude-prefix', required=False, help='Prefix to concat to lines in exclude to produce the full file path')
    optional_args.add_argument('-exclude-basename', required=False, action="store_true", help='Consider an exclude to match if basename matches')
    
    args = parser.parse_args()
    dirs = args.dirs
    min_rows = args.min_rows
    max_rows = args.max_rows
    min_new_rows = args.min_new_rows
    window_factor = args.window_factor
    check_wait_seconds=args.check_wait_seconds
    expand_window_per_row = args.expand_window_per_row
    taper_window_exponent = args.taper_window_exponent
    taper_window_scale = args.taper_window_scale
    add_to_data_rows = args.add_to_data_rows
    if args.add_to_data_rows is not None and args.add_to_window_size is not None:
        print("Cannot specify both -add-to-data-rows and -add-to-window-size. Please use only -add-to-data-rows, -add-to-window-size is deprecated")
    if args.add_to_data_rows is None and args.add_to_window_size is not None:
        print("WARNING: -add-to-window-size is deprecated due to being misnamed, use -add-to-data-rows")
        add_to_data_rows = args.add_to_window_size

    summary_file = args.summary_file
    record_file = args.record_file
    num_processes = args.num_processes
    exclude = args.exclude
    exclude_prefix = args.exclude_prefix
    if exclude_prefix is None:
        exclude_prefix = ""
    exclude_basename = args.exclude_basename

    
    if add_to_data_rows is None:
        add_to_data_rows = 0


    last_check_rows=-1
    last_check_time=time.time()
    
    while True:
        summary_data_by_dirpath = {}
        if summary_file is not None:
            with TimeStuff("Loading " + summary_file):
                # Try a bunch of times, just to be robust to if the file is being swapped out in nfs
                for i in range(10):
                    success = False
                    try:
                        with open(summary_file) as fp:
                            summary_data_by_dirpath = json.load(fp)
                            success = True
                    except OSError:
                        success = False
                    except ValueError:
                        success = False
                    if success:
                        break
                    time.sleep(1)
                if not success:
                    raise RuntimeError("Could not load summary file")
    
        exclude_set = set()
        if exclude is not None:
            with TimeStuff("Loading " + exclude):
                # Try a bunch of times, just to be robust to if the file is being swapped out in nfs
                for i in range(10):
                    success = False
                    try:
                        with open(exclude,"r") as exclude_in:
                            excludes = exclude_in.readlines()
                            excludes = [x.strip() for x in excludes]
                            excludes = [x for x in excludes if len(x) > 0]
                            excludes = [exclude_prefix + x for x in excludes]
                            exclude_set = set(excludes)
                            success = True
                    except OSError:
                        success = False
                    except ValueError:
                        success = False
                    if success:
                        break
                    time.sleep(1)
                if not success:
                    raise RuntimeError("Could not load summary file")
    
        # If excluding basenames, also add them to the set
        if exclude_basename:
            basenames = [os.path.basename(path) for path in exclude_set]
            exclude_set.update(basenames)
    
        all_files = []
        files_with_unknown_num_rows = []
        excluded_count = 0
        excluded_due_to_excludes_count = 0
        tempfilelike_count = 0
        with TimeStuff("Finding files"):
            for d in dirs:
                for (path,dirnames,filenames) in os.walk(d, followlinks=True):
                    i = 0
                    while i < len(dirnames):
                        dirname = dirnames[i]
                        summary_data = summary_data_by_dirpath.get(os.path.abspath(os.path.join(path, dirname)))
                        if summary_data is not None:
                            filename_mtime_num_rowss = summary_data["filename_mtime_num_rowss"]
                            del dirnames[i]
                            i -= 1
                            for (filename,mtime,num_rows) in filename_mtime_num_rowss:
                                if is_temp_npz_like(filename):
                                    #print("WARNING: file looks like a temp file, treating as exclude: ", os.path.join(path,dirname,filename))
                                    excluded_count += 1
                                    tempfilelike_count += 1
                                    continue
                                if exclude_basename and os.path.basename(filename) in exclude_set:
                                    excluded_count += 1
                                    excluded_due_to_excludes_count += 1
                                    continue
                                filename = os.path.join(path,dirname,filename)
                                if not exclude_basename and filename in exclude_set:
                                    excluded_count += 1
                                    excluded_due_to_excludes_count += 1
                                    continue
                                if num_rows is None:
                                    print("WARNING: Skipping bad rowless file, treating as exclude: ", filename)
                                    excluded_count += 1
                                    continue
                                all_files.append((filename,mtime,num_rows))
                        i += 1
    
                    filtered_filenames = []
                    for filename in filenames:
                        if not filename.endswith(".npz"):
                            continue
                        if is_temp_npz_like(filename):
                            # print("WARNING: file looks like a temp file, treating as exclude: ", os.path.join(path,filename))
                            excluded_count += 1
                            tempfilelike_count += 1
                            continue
                        if exclude_basename and os.path.basename(filename) in exclude_set:
                            excluded_count += 1
                            excluded_due_to_excludes_count += 1
                            continue
                        filename = os.path.join(path,filename)
                        if not exclude_basename and filename in exclude_set:
                            excluded_count += 1
                            excluded_due_to_excludes_count += 1
                            continue
                        filtered_filenames.append(filename)
                    filenames = filtered_filenames
    
                    files_with_unknown_num_rows.extend(filenames)
                    filenames = [(filename,os.path.getmtime(filename)) for filename in filenames]
                    all_files.extend(filenames)
        print("Total number of files: %d" % len(all_files), flush=True)
        print("Total number of files with unknown row count: %d" % len(files_with_unknown_num_rows), flush=True)
        if(excluded_count>0):
            print("Excluded count: %d" % excluded_count, flush=True)
            print("Excluded count due to looking like temp file: %d" % tempfilelike_count, flush=True)
            print("Excluded count due to cmdline excludes file: %d" % excluded_due_to_excludes_count, flush=True)
    
        del summary_data_by_dirpath
        gc.collect()
    
        all_files.sort(key=(lambda x: x[1]), reverse=False)
    
        # Wait a few seconds just in case to limit the chance of filesystem races, now that we know exactly
        # the set of filenames we want
        time.sleep(3)
    
        with TimeStuff("Computing rows for unsummarized files"):
            with multiprocessing.Pool(num_processes) as pool:
                results = pool.map(compute_num_rows,files_with_unknown_num_rows)
                results = dict(results)
                for i in range(len(all_files)):
                    info = all_files[i]
                    if len(info) < 3:
                        num_rows = results[info[0]]
                        all_files[i] = (info[0], info[1], num_rows)
    
        num_rows_total = 0 #Number of data rows
        num_random_rows_capped = 0 #Number of random data rows, capped at min_rows - we never keep more than min_rows many data rows if they're from random.
        num_postrandom_rows = 0 #Number of NON-random rows
    
        #How far offset do we start on the power-law window tail? E.g. what number of postrandom rows do we need before the window size grows by a factor
        #of 2^(taper_window_exponent)? For now, we set it equal to the min rows
        if taper_window_scale is not None:
            window_taper_offset = taper_window_scale
        else:
            window_taper_offset = min_rows
    
        def num_usable_rows():
            global num_random_rows_capped
            global num_postrandom_rows
            return num_random_rows_capped + num_postrandom_rows
        def num_desired_rows():
            #Every post-random row moves one row beyond window_taper_offset
            power_law_x = num_usable_rows() - min_rows + window_taper_offset + add_to_data_rows
            #Apply power law and correct for window_taper_offset so we're still anchored at 0
            unscaled_power_law = (power_law_x ** taper_window_exponent) - (window_taper_offset ** taper_window_exponent)
            #Scale so that we have an initial derivative of 1
            scaled_power_law = unscaled_power_law / (taper_window_exponent * (window_taper_offset ** (taper_window_exponent-1)))
            #Scale so that we have the desired initial slope, and add back the minimum random rows
            return int(scaled_power_law * expand_window_per_row + min_rows)
    
        for (filename,mtime,num_rows) in all_files:
            if num_rows is None:
                print("WARNING: Skipping bad file: ", filename)
                continue
            if num_rows <= 0:
                continue
            num_rows_total += num_rows
            if "random/tdata/" not in filename and "random\\tdata\\" not in filename:
                num_postrandom_rows += num_rows
            else:
                num_random_rows_capped = min(num_random_rows_capped + num_rows, min_rows)
    
    
        if num_rows_total <= 0:
            print("No rows found")
            time.sleep(check_wait_seconds)
            continue
    
        #If we don't have enough rows, then quit out
        if num_rows_total < min_rows:
            print("Not enough rows, only %d (fewer than %d)" % (num_rows_total,min_rows))
            time.sleep(check_wait_seconds)
            continue
    
        print("Total rows found: %d (%d usable)" % (num_rows_total,num_usable_rows()), flush=True)
    
        #Reverse so that recent files are first
        all_files.reverse()
    
        #Now assemble only the files we need to hit our desired window size
        desired_num_rows = num_desired_rows()
        usable_num_rows = num_usable_rows()
        desired_num_rows = max(desired_num_rows,min_rows)
        desired_num_rows = min(desired_num_rows,max_rows) if max_rows is not None else desired_num_rows
        print("Desired num rows: %d / %d" % (desired_num_rows,num_rows_total), flush=True)
    
        
        last_checkpoint={"last_rows":usable_num_rows,"expect_rows":usable_num_rows,"last_window":desired_num_rows}
        if(os.path.exists(record_file)):
            with open(record_file, 'r') as f:
                last_checkpoint=json.load(f)
        expect_rows=last_checkpoint["expect_rows"]
        if(usable_num_rows>=expect_rows):
            print(f"Found enough rows {usable_num_rows}, expected {expect_rows}, exit", flush=True)
            new_expect_rows=max(expect_rows,usable_num_rows-desired_num_rows)
            new_expect_rows+=min_new_rows
            if(new_expect_rows<usable_num_rows):
                window_rate=window_factor*(usable_num_rows-new_expect_rows)/desired_num_rows
                new_expect_rows+=window_rate*min_new_rows
                new_expect_rows=int(new_expect_rows)
                print(f"Too many new rows, move extra {math.floor(window_rate*min_new_rows)} rows ({0.1*round(window_rate*1000)}%)", flush=True)
        
            checkpoint_info={"last_rows":usable_num_rows,"expect_rows":new_expect_rows,"last_window":desired_num_rows}
            
            
            with open(record_file, 'w') as f:
                json.dump(checkpoint_info, f)
            break
        else:
            print(f"No enough rows {usable_num_rows}, expected {expect_rows}, waiting {check_wait_seconds} s and retry, {datetime.datetime.now()}", flush=True)

            zhizi_row_per_xs=7.7 #~rtx2060 per second
            new_check_time=time.time()
            if(last_check_rows!=-1):
                newrows=usable_num_rows-last_check_rows
                avgspeed=newrows/(new_check_time-last_check_time)
                print(f"New row num:{newrows}, speed {avgspeed} row/s, equal to {avgspeed/zhizi_row_per_xs} x", flush=True)
            
            last_check_rows=usable_num_rows
            last_check_time=new_check_time
            
            time.sleep(check_wait_seconds)
            continue
