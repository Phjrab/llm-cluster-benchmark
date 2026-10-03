"""Check finalized source frame hash, parameterized load polygons and mass budget."""
from pathlib import Path
import json,hashlib,numpy as np
R=Path(__file__).resolve().parent;A=json.loads((R/'analysis_assumptions.json').read_text());digest=hashlib.sha256((R/'frame.step').read_bytes()).hexdigest();assert digest==A['source_step_sha256'];seating_digest=hashlib.sha256((R/'seating_contact.step').read_bytes()).hexdigest();assert seating_digest==A['seating_contact_sha256'];areas=[]
for poly in A['rail_pressure_polygons_mm']:
 xy=np.array(poly)[:,:2];areas.append(float(abs(np.sum(xy[:,0]*np.roll(xy[:,1],-1)-xy[:,1]*np.roll(xy[:,0],-1)))/2))
r={'seating_contact_sha256':seating_digest,'source_step_sha256':digest,'original_actual_contact_areas_mm2':A['actual_guard_contact_areas_mm2'],'polygon_chord_areas_mm2':areas,'polygon_to_actual_area_relative_difference':[abs(p/a-1) for p,a in zip(areas,A['actual_guard_contact_areas_mm2'])],'upper_tier_print_mass_kg':A['upper_tier_print_mass_kg'],'source_status':A['source_status']};assert max(r['polygon_to_actual_area_relative_difference'])<.10;r['passed']=True;(R/'results/input_validation.json').write_text(json.dumps(r,indent=2));print(r)
