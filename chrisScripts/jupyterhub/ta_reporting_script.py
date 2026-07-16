#!/usr/bin/env python3
import os
import json
import glob

# The root directory on the NAS where all student workspaces are mounted
CLASS_WORK_DIR = "/mnt/production_uploads/class_work"

def generate_ta_report():
    print(f"{'Student':<20} | {'Total Interactions':<20} | {'AI Code Lines Generated':<25}")
    print("-" * 70)
    
    # Iterate through every student's persistent chat history file
    history_files = glob.glob(f"{CLASS_WORK_DIR}/*/.local_share/jupyter/jupyter_ai/chat_history.json")
    
    for filepath in history_files:
        # Extract username from the path: /mnt/production_uploads/class_work/{username}/.local_share/...
        username = filepath.split("/")[-5]
        
        try:
            with open(filepath, 'r') as f:
                history = json.load(f)
                
            total_messages = len(history)
            ai_code_lines = 0
            
            for msg in history:
                # Calculate how much code the AI returned by counting lines in markdown blocks
                if msg.get("sender") == "ai":
                    body = msg.get("body", "")
                    if "```" in body:
                        # Very rough estimation: count lines between backticks
                        code_blocks = body.split("```")[1::2]
                        for block in code_blocks:
                            ai_code_lines += len(block.split('\n')) - 2 # minus language tag and empty lines
                            
            print(f"{username:<20} | {total_messages:<20} | {ai_code_lines:<25}")
            
        except Exception as e:
            print(f"{username:<20} | Error parsing logs: {e}")

if __name__ == '__main__':
    generate_ta_report()
