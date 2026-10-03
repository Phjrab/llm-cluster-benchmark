import pathlib,sys,numpy as np,json,os,csv
R=pathlib.Path(__file__).resolve().parent;DEP=pathlib.Path(os.environ.get('STUDY_DEPS',str(R.parent/'jetson-pla-study/tools')));sys.path.insert(0,str(DEP/'python'));import gmsh
os.environ['MPLCONFIGDIR']=str(R/'tmp/mpl');import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
A=json.loads((R/'analysis_assumptions.json').read_text());S=json.loads((R/'results/summary.json').read_text())
for kind in ['frame']:
 h=A[kind+'_mesh_h_mm'][-1];out=R/'results'/f'{kind}-h{h:g}';z=np.load(out/'mesh.npz');p=z['coordinates'];nd={n:i for i,n in enumerate(z['nodes'])};fld=np.load(out/'fields.npz');gmsh.initialize();gmsh.open(str(out/f'{kind}.msh'));_,tri=gmsh.model.mesh.getElementsByType(9);t=np.array(tri).reshape(-1,6)[:,:3];gmsh.finalize();t=np.array([[nd[n] for n in row] for row in t]);verts=p[t];fig=plt.figure(figsize=(12,5),facecolor='white')
 for j,(field,title,label) in enumerate([(np.linalg.norm(fld['displacement'],axis=1),'Static displacement','Displacement [mm]'),(fld['von_mises'],'Continuum equivalent stress','von Mises [MPa]')]):
  ax=fig.add_subplot(1,2,j+1,projection='3d');norm=plt.Normalize(0,float(field.max()));coll=Poly3DCollection(verts,facecolors=plt.cm.viridis(norm(field[t].mean(axis=1))),linewidths=0,rasterized=True);ax.add_collection3d(coll)
  lo=p.min(axis=0);hi=p.max(axis=0)
  ax.set_xlim(lo[0],hi[0]);ax.set_ylim(lo[1],hi[1]);ax.set_zlim(lo[2],hi[2]);ax.set_box_aspect(hi-lo);ax.view_init(elev=27,azim=-56);ax.set_xlabel('X [mm]');ax.set_ylabel('Y [mm]');ax.set_zlabel('Z [mm]');ax.set_title(title);fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap='viridis'),ax=ax,shrink=.65,pad=.11,label=label)
 fig.suptitle(A['design']+' | '+kind+' | actual STEP | undeformed shape',fontsize=13);fig.text(.5,.018,f'CalculiX2.20 / C3D10 / h={h:g}mm / effective E=1000MPa / gravity only\nSurface face-averaged real solver fields; no print lattice, joint retention, contact, heat or creep',ha='center',fontsize=8);fig.subplots_adjust(left=.015,right=.97,top=.9,bottom=.13,wspace=.16);fig.savefig(R/f'{kind}_static_results.png',dpi=160);plt.close(fig)
q=np.linspace(min(A['thermal_effective_flow_CFM']),max(A['thermal_effective_flow_CFM']),240);fig,ax=plt.subplots(figsize=(7.6,4.3));rho=A['air_density_kg_m3'];cp=A['air_cp_J_kgK'];cfm=.00047194745
for watts in A['thermal_total_power_W']:ax.plot(q,25+watts/(rho*cp*cfm*q),label=f'{watts} W total')
if 'fan_max_airflow_CFM_each' in A:ax.axvline(3*A['fan_max_airflow_CFM_each'],color='grey',linestyle=':',label='3 x nominal max (not actual flow)')
ax.set_xlabel('Effective heat-carrying stack airflow [CFM], assumed');ax.set_ylabel('Ideal mixed-air outlet [C]');ax.set_title('Air energy-balance sensitivity | ambient25 C');ax.grid(alpha=.2);ax.legend(fontsize=8);fig.text(.5,.006,'Not CFD or PLA/CPU temperature; unknown recirculation, hotspots and conduction',ha='center',fontsize=8);fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(R/'airflow_sensitivity.png',dpi=160);plt.close(fig)
with (R/'mesh_convergence.csv').open('w') as f:
 keys=['kind','h_mm','nodes','elements_C3D10','max_displacement_mm','max_vertical_displacement_mm','stress_von_mises_peak_MPa','reaction_balance_relative_error','geometry_volume_relative_error'];w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows({k:r[k] for k in keys} for r in S['meshes'])
with (R/'stiffness_sensitivity.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['kind','E_MPa','max_displacement_mm','method'])
 for kind in ['frame']:
  r=[r for r in S['meshes'] if r['kind']==kind][-1]
  for e in A['E_sensitivity_MPa']:w.writerow([kind,e,r['max_displacement_mm']*1000/e,'linear1/E scaling, not additional solver/calibrated bound'])
with (R/'thermal_sensitivity.csv').open('w') as f:
 w=csv.writer(f);w.writerow(['power_W','effective_CFM','ambient_C','ideal_mixed_air_C','method'])
 for watts in A['thermal_total_power_W']:
  for flow in A['thermal_effective_flow_CFM']:
   for temp in A['thermal_ambient_C']:w.writerow([watts,flow,temp,temp+watts/(rho*cp*cfm*flow),'energy balance only, not PLA temperature'])

# Specified load boundary visualization, explicitly not solver stress/thermal field.
from matplotlib.patches import Rectangle,Circle,Polygon
fig,ax=plt.subplots(figsize=(7.4,4.7));ax.add_patch(Rectangle((0,0),142,100,fill=False,edgecolor='grey',linewidth=1.5))
gmsh.initialize();gmsh.model.occ.importShapes(str(R/'seating_contact.step'));gmsh.model.occ.synchronize();gmsh.option.setNumber('Mesh.MeshSizeMin',1);gmsh.option.setNumber('Mesh.MeshSizeMax',1);gmsh.model.mesh.generate(2);tags,xyz,_=gmsh.model.mesh.getNodes();xyz=np.array(xyz).reshape(-1,3);nd=dict(zip(tags,xyz));_,connect=gmsh.model.mesh.getElementsByType(2);triangles=np.array(connect).reshape(-1,3);gmsh.finalize()
from matplotlib.collections import PolyCollection
ax.add_collection(PolyCollection([np.array([nd[n][:2] for n in t]) for t in triangles],facecolors='#5b9bd5',edgecolors='none',alpha=.8,label='Upper2tier seated contact(z60)'))
for i,poly in enumerate(A['rail_pressure_polygons_mm']):ax.add_patch(Polygon(np.array(poly)[:,:2],facecolor='#ef7355',edgecolor='#b4381f',label='Kit300g rail pressure(z8)' if i==0 else None))
ax.set_aspect('equal');ax.set_xlim(-4,146);ax.set_ylim(-4,105);ax.set_xlabel('X[mm]');ax.set_ylabel('Y[mm]');ax.set_title('Specified gravity pressure footprints | XY projection');ax.legend(loc='center',fontsize=9);ax.grid(alpha=.15);fig.text(.5,.012,'Blue is exact owned seated-contact CAD. Red is actual-guard contact polygon approximation.\nLoad-boundary diagram only, not stress, deformation, flow or joint-retention result.',ha='center',fontsize=8);fig.tight_layout(rect=[0,.07,1,1]);fig.savefig(R/'load_footprints.png',dpi=160);plt.close(fig)
