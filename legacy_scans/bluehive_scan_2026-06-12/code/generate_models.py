import numpy as np
import os
import sys
import subprocess

RUN_BIN = '/software/litho/1.0/bin/access_litho'

# INLAND CONTINENTAL LOCATIONS ONLY - NO WATER
TECTONIC_ENVIRONMENTS = [
    {'name': 'Central_US_Continental', 'lat': 40.0, 'lon': -100.0, 'desc': 'Central US Continental'},
    {'name': 'East_African_Rift', 'lat': -5.0, 'lon': 35.0, 'desc': 'East African Rift'},
    {'name': 'Australian_Interior', 'lat': -25.0, 'lon': 135.0, 'desc': 'Australian Craton Interior'},
    {'name': 'Siberian_Craton', 'lat': 62.0, 'lon': 100.0, 'desc': 'Siberian Craton'},
    {'name': 'Arabian_Shield', 'lat': 22.0, 'lon': 42.0, 'desc': 'Arabian Shield'},
]

def run_litho_accessor_and_get_data(lat, lon, run_bin_path=RUN_BIN):
    """Call Litho1.0 accessor to get Earth model at location"""
    command = [run_bin_path, '-p', str(lat), str(lon)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Litho accessor failed: {e}")
        sys.exit(1)

def load_and_process_data(raw_data_string):    
    """Parse Litho1.0 output and convert to CPS model format"""
    parsed_data = []
    layer_labels = []
    
    # Parse raw data
    for line in raw_data_string.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            depth_m = float(parts[0])
            rho_kgm3 = float(parts[1])
            vp_ms = float(parts[2])
            vs_ms = float(parts[3])
            label = parts[9]
            
            if label == 'INTERFACE':
                continue
                
            parsed_data.append([depth_m, rho_kgm3, vp_ms, vs_ms])
            layer_labels.append(label)
        except (ValueError, IndexError):
            continue
    
    if not parsed_data:
        raise ValueError("No valid data parsed from Litho output")
    
    # Remove duplicate depths
    seen = {}
    unique_data = []
    unique_labels = []
    for data, label in zip(parsed_data, layer_labels):
        depth = data[0]
        if depth not in seen:
            seen[depth] = True
            unique_data.append(data)
            unique_labels.append(label)
    
    raw_model = np.array(unique_data)
    
    # DEBUG: Print raw depths
    print(f"  Raw depths (first 5): {raw_model[:5, 0]}")
    print(f"  Raw depths (last 5): {raw_model[-5:, 0]}")
    
    # Litho1.0 uses NEGATIVE depths (below surface), convert to positive
    depths_m = np.abs(raw_model[:, 0])
    
    # Sort by depth (shallow to deep)
    sort_idx = np.argsort(depths_m)
    depths_m = depths_m[sort_idx]
    rho_kgm3 = raw_model[sort_idx, 1]
    vp_ms = raw_model[sort_idx, 2]
    vs_ms = raw_model[sort_idx, 3]
    unique_labels = [unique_labels[i] for i in sort_idx]
    
    print(f"  Sorted depths (first 5): {depths_m[:5]}")
    print(f"  Extracted {len(unique_data)} unique layers")
    
    # Unit conversions
    depths_km = depths_m / 1000.0
    vp_kms = vp_ms / 1000.0
    vs_kms = vs_ms / 1000.0
    rho_gcc = rho_kgm3 / 1000.0
    
    # Calculate thicknesses (depths sorted shallow → deep)
    thicknesses_km = np.zeros(len(depths_km))
    thicknesses_km[0] = depths_km[0]  # Surface to first depth
    
    for i in range(1, len(depths_km)):
        thicknesses_km[i] = depths_km[i] - depths_km[i-1]
    
    # DEBUG: Print thicknesses
    print(f"  Thicknesses (first 5): {thicknesses_km[:5]}")
    
    if np.any(thicknesses_km < -1e-6):  # Allow small numerical errors
        print(f"  ❌ Negative thicknesses found!")
        print(f"  Depths: {depths_km}")
        print(f"  Thicknesses: {thicknesses_km}")
        raise ValueError("Negative thicknesses - depth ordering issue")
    
    # Treat very small negative values as zero
    thicknesses_km = np.abs(thicknesses_km)
    
    # Remove zero-thickness layers
    nonzero_mask = thicknesses_km > 1e-6
    
    if not np.all(nonzero_mask):
        n_removed = np.sum(~nonzero_mask)
        print(f"  Removing {n_removed} zero-thickness layer(s)")
        thicknesses_km = thicknesses_km[nonzero_mask]
        vp_kms = vp_kms[nonzero_mask]
        vs_kms = vs_kms[nonzero_mask]
        rho_gcc = rho_gcc[nonzero_mask]
        unique_labels = [l for l, keep in zip(unique_labels, nonzero_mask) if keep]
    
    # REMOVE WATER LAYERS (VS < 0.1 km/s or RHO < 1.5 g/cc)
    water_mask = (vs_kms < 0.1) | (rho_gcc < 1.5)
    if np.any(water_mask):
        n_water = np.sum(water_mask)
        print(f"  REMOVING {n_water} water/fluid layer(s)")
        solid_mask = ~water_mask
        thicknesses_km = thicknesses_km[solid_mask]
        vp_kms = vp_kms[solid_mask]
        vs_kms = vs_kms[solid_mask]
        rho_gcc = rho_gcc[solid_mask]
        unique_labels = [l for l, keep in zip(unique_labels, solid_mask) if keep]
    
    if len(thicknesses_km) == 0:
        raise ValueError("No solid layers remaining after water removal")
    
    processed_model = np.column_stack([thicknesses_km, vp_kms, vs_kms, rho_gcc])
    
    print(f"  Final model: {len(thicknesses_km)} solid layers")
    print(f"  Total thickness: {np.sum(thicknesses_km):.2f} km")
    print(f"  VP range: {vp_kms.min():.3f} - {vp_kms.max():.3f} km/s")
    print(f"  VS range: {vs_kms.min():.3f} - {vs_kms.max():.3f} km/s")
    print(f"  RHO range: {rho_gcc.min():.3f} - {rho_gcc.max():.3f} g/cc")
    
    return processed_model
def write_cps_model96(model_data, output_filename='MODEL.01', description='Litho1.0'):
    """Write CPS MODEL96 format with halfspace"""
    
    with open(output_filename, 'w') as f:
        f.write("MODEL.01\n")
        f.write(f"{description[:79]}\n")
        f.write("ISOTROPIC\n")
        f.write("KGS\n")
        f.write("SPHERICAL EARTH\n")
        f.write("1-D\n")
        f.write("CONSTANT VELOCITY\n")
        f.write("LINE08\nLINE09\nLINE10\nLINE11\n")
        f.write("      H(KM)   VP(KM/S)   VS(KM/S) RHO(GM/CC)         QP         QS       ETAP       ETAS      FREFP      FREFS\n")
        
        # Write all layers with appropriate Q values
        for i in range(model_data.shape[0]):
            H, VP, VS, RHO = model_data[i, :]
            
            # Determine Q values based on layer properties
            if VS < 3.0:  # Sediments
                QP, QS = 600.0, 300.0
            elif VS < 4.2:  # Crust
                QP, QS = 600.0, 300.0
            elif VS < 4.6:  # Upper mantle (lithosphere)
                QP, QS = 1500.0, 600.0
            else:  # Deeper mantle
                QP, QS = 1200.0, 600.0
            
            f.write(f"  {H:8.4f}   {VP:9.4f}   {VS:9.4f}   {RHO:9.4f}   {QP:7.1f}   {QS:7.1f}       0.00       0.00       1.00       1.00\n")
        
        # Add halfspace (H=0, same properties as last layer)
        H_half = 0.0
        VP_half = model_data[-1, 1]
        VS_half = model_data[-1, 2]
        RHO_half = model_data[-1, 3]
        QP_half, QS_half = 1200.0, 600.0  # Mantle Q values
        
        f.write(f"  {H_half:8.4f}   {VP_half:9.4f}   {VS_half:9.4f}   {RHO_half:9.4f}   {QP_half:7.1f}   {QS_half:7.1f}       0.00       0.00       1.00       1.00\n")
    
    print(f"  Created: {output_filename} ({model_data.shape[0] + 1} layers including halfspace)")

def validate_model(model_data, model_name="Model"):
    """Validate model for physical consistency"""
    print(f"\n  VALIDATION: {model_name}")
    
    H = model_data[:, 0]
    VP = model_data[:, 1]
    VS = model_data[:, 2]
    RHO = model_data[:, 3]
    
    # Check thicknesses
    if np.any(H <= 0):
        print(f"   ❌ FAIL: Non-positive thicknesses")
        return False
    else:
        print(f"   ✓ PASS: All thicknesses positive")
    
    # Check velocities
    if np.any(VP <= 0) or np.any(VS <= 0):
        print(f"   ❌ FAIL: Non-positive velocities")
        return False
    else:
        print(f"   ✓ PASS: All velocities positive")
    
    # Check VP > VS
    vp_vs_ratio = VP / VS
    if np.any(vp_vs_ratio <= 1.0):
        print(f"   ⚠ WARNING: Some layers have VP ≤ VS")
    else:
        print(f"   ✓ PASS: VP > VS in all layers")
    
    # Check density
    if np.any(RHO <= 0):
        print(f"   ❌ FAIL: Non-positive density")
        return False
    
    # Check for water (should be removed)
    if np.any(VS < 0.1) or np.any(RHO < 1.5):
        print(f"   ❌ FAIL: Water layers still present")
        return False
    else:
        print(f"   ✓ PASS: No water layers")
    
    print(f"   Total depth: {np.sum(H):.2f} km")
    
    # Estimate crustal thickness (VS < 4.2 km/s = crust)
    mantle_layers = VS >= 4.2
    if np.any(mantle_layers):
        first_mantle_idx = np.where(mantle_layers)[0][0]
        crust_thickness = np.sum(H[:first_mantle_idx])
        print(f"   Crustal thickness: {crust_thickness:.2f} km")
    
    return True

def generate_model_suite():
    """Generate complete suite of inland continental models"""
    print("\n" + "="*70)
    print("GENERATING LITHO1.0 INLAND CONTINENTAL MODEL SUITE")
    print("="*70)
    
    output_dir = 'experiments/model_suite'
    os.makedirs(output_dir, exist_ok=True)
    
    successful = 0
    failed = 0
    
    for i, env in enumerate(TECTONIC_ENVIRONMENTS, start=1):
        print(f"\n[{i}/{len(TECTONIC_ENVIRONMENTS)}] {env['name']}")
        print(f"        Location: {env['lat']}°, {env['lon']}°")
        print(f"        Description: {env['desc']}")
        
        try:
            # Get Litho1.0 data
            raw = run_litho_accessor_and_get_data(env['lat'], env['lon'])
            
            # Process into model
            model = load_and_process_data(raw)
            
            # Validate
            if validate_model(model, env['name']):
                # Write to file
                fname = f"{output_dir}/{env['name']}.mod"
                write_cps_model96(model, fname, env['desc'])
                successful += 1
            else:
                print(f"   ❌ Validation failed")
                failed += 1
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "="*70)
    print(f"MODEL SUITE GENERATION COMPLETE")
    print(f"  Successful: {successful}/{len(TECTONIC_ENVIRONMENTS)}")
    print(f"  Failed: {failed}/{len(TECTONIC_ENVIRONMENTS)}")
    print("="*70)
    
    if successful > 0:
        print(f"\nModels saved in: {output_dir}/")
        print("Next steps:")
        print("  1. Verify models: ls -lh experiments/model_suite/*.mod")
        print("  2. Check a model: head -20 experiments/model_suite/Central_US_Continental.mod")
        print("  3. Use in WaveNET experiments")

def main():
    try:
        generate_model_suite()
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
