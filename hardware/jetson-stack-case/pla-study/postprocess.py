import json,pathlib,re,sys
import numpy as np
R=pathlib.Path(__file__).resolve().parent

def frd(path):
 d={};name=None
 for line in path.read_text().splitlines():
  if line.startswith(' -4 '):name=line.split()[1];d[name]={}
  elif line.startswith(' -1 ') and name:
   n=int(line[3:13]);d[name][n]=[float(line[i:i+12]) for i in range(13,len(line),12) if line[i:i+12].strip()]
  elif line.startswith(' -3'):name=None
 return d
b=frd(R/'results/benchmark/patch.frd');uz=np.array(list(b['DISP'].values()))[:,2];s=np.array(list(b['STRESS'].values()));bn=[]
for ln in (R/'results/benchmark/patch.inp').read_text().split('*ELEMENT')[0].splitlines()[1:]:
 x=ln.split(',');
 if len(x)==4 and abs(float(x[3]))<1e-8:bn.append(int(x[0]))
bf=np.array([b['FORC'][n] for n in bn])
bench={'max_compression_mm':float(-uz.min()),'analytical_compression_mm':.001,'relative_displacement_error':float(abs(uz.min()+.001)/.001),'stress_zz_range_MPa':[float(s[:,2].min()),float(s[:,2].max())],'reaction_z_N':float(bf[:,2].sum()),'passed':bool(abs(uz.min()+.001)<1e-7 and abs(bf[:,2].sum()-10)<1e-3)}
(R/'results/benchmark/check.json').write_text(json.dumps(bench,indent=2));assert bench['passed'],bench
result=[]
for h in [4.,2.8]:
 out=R/'results'/f'h{h:g}'
 if not (out/'frame.frd').exists():continue
 d=frd(out/'frame.frd');m=json.loads((out/'metadata.json').read_text());z=np.load(out/'mesh.npz');nodes=z['nodes'];p=z['coordinates']
 u=np.array([d['DISP'][int(n)] for n in nodes]);s=np.array([d['STRESS'][int(n)] for n in nodes]);f=np.array([d['FORC'][int(n)] for n in nodes])
 sx,sy,sz,xy,yz,zx=s.T;vm=np.sqrt(.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(xy**2+yz**2+zx**2));um=np.linalg.norm(u,axis=1)
 rail=(abs(p[:,2]-8)<1e-6)&(((p[:,0]>=17.8-1e-6)&(p[:,0]<=22.4+1e-6))|((p[:,0]>=119.6-1e-6)&(p[:,0]<=124.2+1e-6)))&(p[:,1]>=2)&(p[:,1]<=92)
 mask=p[:,2]>2
 # RF in CCX is the sum of reactions AND loads at the same nodes.
 # Remove consistent tetra10 gravity loads at constrained support nodes.
 nd=dict(zip(nodes,p));sp=set(z['supports']);g_support=0.
 for c in z['connectivity']:
  q=np.array([nd[n] for n in c[:4]]);vol=abs(np.linalg.det((q[1:]-q[0]).T))/6
  for i,n in enumerate(c):
   if n in sp:g_support+=-vol*1.24e-9*9810*(-.05 if i<4 else .2)
 raw_rf=sum(d['FORC'][int(n)][2] for n in z['supports'])
 reaction=raw_rf-g_support
 r={**m,'max_displacement_mm':float(um.max()),'max_vertical_displacement_mm':float(-u[:,2].min()),'rail_max_vertical_mm':float(-u[rail,2].min()),'stress_von_mises_peak_MPa':float(vm.max()),'stress_von_mises_peak_location_mm':p[vm.argmax()].tolist(),'stress_von_mises_peak_excluding_bottom2mm_MPa':float(vm[mask].max()),'stress_von_mises_node_p95_MPa':float(np.percentile(vm,95)),'rf_sum_at_supports_N':float(raw_rf),'gravity_load_at_supports_N':float(g_support),'reaction_z_N':float(reaction),'reaction_balance_relative_error':float(abs(reaction-m['total_load_N_linearized'])/m['total_load_N_linearized']),'geometry_volume_relative_error':float(abs(m['mesh_linearized_volume_mm3']/m['cad_volume_mm3']-1))}
 (out/'summary.json').write_text(json.dumps(r,indent=2));np.savez_compressed(out/'fields.npz',displacement=u,stress=s,von_mises=vm)
 result.append(r)
(R/'results/summary.json').write_text(json.dumps({'benchmark':bench,'meshes':result},indent=2))
print(json.dumps({'benchmark':bench,'meshes':result},indent=2))
