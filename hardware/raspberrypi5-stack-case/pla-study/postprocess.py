import json,pathlib,numpy as np
R=pathlib.Path(__file__).resolve().parent;A=json.loads((R/'analysis_assumptions.json').read_text())
def frd(path):
 d={};name=None
 for line in path.read_text().splitlines():
  if line.startswith(' -4 '):name=line.split()[1];d[name]={}
  elif line.startswith(' -1 ') and name:d[name][int(line[3:13])]=[float(line[i:i+12]) for i in range(13,len(line),12) if line[i:i+12].strip()]
  elif line.startswith(' -3'):name=None
 return d
result=[]
for kind in ['frame','pin']:
 for h in A[kind+'_mesh_h_mm']:
  out=R/'results'/f'{kind}-h{h:g}';d=frd(out/f'{kind}.frd');m=json.loads((out/'metadata.json').read_text());z=np.load(out/'mesh.npz');nodes=z['nodes'];p=z['coordinates'];u=np.array([d['DISP'][int(n)] for n in nodes]);s=np.array([d['STRESS'][int(n)] for n in nodes]);sx,sy,sz,xy,yz,zx=s.T;vm=np.sqrt(.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(xy**2+yz**2+zx**2));um=np.linalg.norm(u,axis=1);nd=dict(zip(nodes,p));sp=set(z['supports']);g_support=0.
  for c in z['connectivity']:
   q=np.array([nd[n] for n in c[:4]]);vol=abs(np.linalg.det((q[1:]-q[0]).T))/6
   for i,n in enumerate(c):
    if n in sp:g_support+=-vol*A['density_tonne_mm3']*9810*(-.05 if i<4 else .2)
  raw=sum(d['FORC'][int(n)][2] for n in sp);reaction=raw-g_support
  r={**m,'max_displacement_mm':float(um.max()),'max_vertical_displacement_mm':float(-u[:,2].min()),'stress_von_mises_peak_MPa':float(vm.max()),'stress_peak_location_mm':p[vm.argmax()].tolist(),'stress_node_p95_MPa':float(np.percentile(vm,95)),'reaction_z_N':float(reaction),'reaction_balance_relative_error':float(abs(reaction-m['total_load_N_linearized'])/m['total_load_N_linearized']),'geometry_volume_relative_error':float(abs(m['mesh_linearized_volume_mm3']/m['cad_volume_mm3']-1))}
  assert r['reaction_balance_relative_error']<1e-4,r
  (out/'summary.json').write_text(json.dumps(r,indent=2));np.savez_compressed(out/'fields.npz',displacement=u,stress=s,von_mises=vm);result.append(r)
benchpath=R/'results/benchmark';b=frd(benchpath/'patch.frd');uz=np.array(list(b['DISP'].values()))[:,2];stress=np.array(list(b['STRESS'].values()));bottom=[]
for line in (benchpath/'patch.inp').read_text().split('*ELEMENT')[0].splitlines()[1:]:
 v=line.split(',')
 if len(v)==4 and abs(float(v[3]))<1e-8:bottom.append(int(v[0]))
rf=sum(b['FORC'][n][2] for n in bottom);bench={'analytical_compression_mm':.001,'actual_compression_mm':float(-uz.min()),'reaction_N':rf,'expected_reaction_N':10,'stress_zz_range_MPa':[float(stress[:,2].min()),float(stress[:,2].max())],'passed':abs(uz.min()+.001)<1e-7 and abs(rf-10)<1e-3};assert bench['passed'];(benchpath/'check.json').write_text(json.dumps(bench,indent=2))
convergence={}
for kind in ['frame','pin']:
 a,b=[r for r in result if r['kind']==kind]
 convergence[kind]={key:100*abs(b[key]-a[key])/abs(b[key]) for key in ['max_displacement_mm','max_vertical_displacement_mm','stress_von_mises_peak_MPa']}
(R/'results/summary.json').write_text(json.dumps({'benchmark':bench,'meshes':result,'coarse_to_fine_change_percent_relative_fine':convergence},indent=2));print(json.dumps({'meshes':result,'convergence':convergence},indent=2))
