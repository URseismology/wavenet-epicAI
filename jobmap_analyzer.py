import sys
import os
import csv
import glob

def get_expected_folder_name(config_path, model_path):
    """
    Derives the expected output folder name from the config and model paths.
    E.g., 
    config: ./experiments/experiment_1/configs/SIM_00001_ang_0_36_dist_200_rad_150_1000.txt
    model: ./model_suite/tak135sph.mod
    Returns: sim_00001_ang_0_36_dist_200_rad_150_1000_tak135sph
    """
    config_name = os.path.basename(config_path).replace('.txt', '').lower()
    model_name = os.path.basename(model_path).replace('.mod', '')
    
    return f"{config_name}_{model_name}"

def analyze_toy_example():
    """
    Runs the analysis using the downloaded toy text files.
    Chris can adapt this function to use glob.glob() directly on the epic_production folder.
    """
    job_map_file = "toy_job_map.csv"
    folders_file = "toy_completed_folders.txt"
    
    if not os.path.exists(job_map_file) or not os.path.exists(folders_file):
        print(f"Error: Could not find {job_map_file} or {folders_file}")
        sys.exit(1)
        
    # Read the completed paths from the NAS dump
    with open(folders_file, 'r') as f:
        completed_paths = f.read().splitlines()
        
    # We only care about the final directory name to cross-reference
    completed_set = set(os.path.basename(p) for p in completed_paths)
    
    finished_rows = []
    unfinished_rows = []
    
    # Parse the job map and separate into finished/unfinished
    with open(job_map_file, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row: continue # Skip empty lines
            
            # Row structure: [ID, Config_Path, Model_Path, Output_Path]
            config_path = row[1]
            model_path = row[2]
            
            expected_folder = get_expected_folder_name(config_path, model_path)
            
            # Diff logic
            if expected_folder in completed_set:
                finished_rows.append(row)
            else:
                unfinished_rows.append(row)
                
    # Write the separated job maps
    with open('jobmap_finished.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(finished_rows)
        
    with open('jobmap_unfinished.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(unfinished_rows)
        
    # Output statistics
    total = len(finished_rows) + len(unfinished_rows)
    print("\n=== Toy Example Analysis Complete ===")
    print(f"Total Jobs in Map : {total}")
    print(f"Finished Jobs     : {len(finished_rows)} ({(len(finished_rows)/total)*100:.1f}%)")
    print(f"Unfinished Jobs   : {len(unfinished_rows)}")
    print("=====================================\n")

if __name__ == "__main__":
    analyze_toy_example()
