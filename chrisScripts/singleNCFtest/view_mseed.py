import obspy
import os
import matplotlib.pyplot as plt

# Path to one of the downloaded miniseed files
file_path = "raw_data/CI.PAS/miniseed/CI/1991/144/PAS.CI.1991.144#1"

print(f"Loading {file_path}...")
if not os.path.exists(file_path):
    print("File not found! Make sure you run this script from the singleNCFtest directory.")
    exit(1)

# Read the stream
st = obspy.read(file_path)

# Print basic stats
print("Stream info:")
print(st)

# Plot the stream and save to an image
plot_path = "miniseed_plot.png"
st.plot(outfile=plot_path)
print(f"\nPlot saved to {plot_path}")
