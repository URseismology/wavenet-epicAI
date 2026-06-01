import numpy as np
from scipy.signal import butter, filtfilt, hilbert, detrend
from scipy.signal.windows import tukey

def cos_taper(data, alpha=0.05):
    """
    Apply a cosine taper (Tukey window) to the ends of the data array.
    This matches common seismological tapering (e.g., in SAC/ObsPy).
    """
    window = tukey(len(data), alpha)
    return data * window

def get_filter_TFcoeffs(frange, dt):
    """
    Generate bandpass filter coefficients for multiple narrow bands,
    matching ADAMA's get_filter_TFcoeffs.m logic.
    
    frange: [fmin, fmax] in Hz
    dt: sample spacing in seconds (1/sampling_rate)
    """
    frange = sorted(frange)
    df = frange[0] / 4.0  # 1/4 of lowest frequency
    
    # Emulate MATLAB's start:step:stop which includes the upper bound if it fits
    freqs = np.arange(frange[0], frange[1] + (df / 2.0), df)
    
    fN = 1.0 / (2.0 * dt)
    
    b_mat = []
    a_mat = []
    
    for i in range(len(freqs) - 1):
        f1 = freqs[i] / fN
        f2 = freqs[i+1] / fN
        
        # Guard against Nyquist
        if f2 >= 1.0:
            f2 = 0.999
            
        b, a = butter(2, [f1, f2], btype='bandpass')
        b_mat.append(b)
        a_mat.append(a)
        
    return b_mat, a_mat

def apply_ftn(data, b_mat, a_mat):
    """
    Apply Frequency-Time Normalization (Shen et al. 2012)
    Matches ADAMA's FTN.m logic.
    """
    # 1. Initial Detrend and Taper
    data = detrend(data)
    data = cos_taper(data)
    
    dataf = np.zeros((len(data), len(b_mat)))
    
    # 2. Filter in narrow bands
    for i in range(len(b_mat)):
        b = b_mat[i]
        a = a_mat[i]
        
        filtered = filtfilt(b, a, data)
        filtered = detrend(filtered)
        filtered = cos_taper(filtered)
        dataf[:, i] = filtered
        
    # 3. Calculate envelope and normalize each band
    envelope = np.abs(hilbert(dataf, axis=0))
    
    # Avoid division by zero by replacing 0s with a tiny number
    envelope[envelope == 0] = np.finfo(float).eps
    
    norm_dataf = dataf / envelope
    
    # 4. Sum all normalized frequency bands
    data_FTN = np.sum(norm_dataf, axis=1)
    
    # 5. Final Detrend and Taper
    data_FTN = detrend(data_FTN)
    data_FTN = cos_taper(data_FTN)
    
    return data_FTN
