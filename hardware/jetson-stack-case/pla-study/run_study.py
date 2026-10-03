"""Headless Gmsh 4.13.1 / CalculiX 2.20 preliminary static continuum screening.
Units mm N MPa tonne. No contact, lattice, creep or CFD solver is implied.
"""
import sys,os,json,hashlib,subprocess, pathlib, time
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tools/python'))
import gmsh
SRC=ROOT/'frame.step'
if not SRC.exists():SRC=ROOT.parent/'jetson-stack-case/rounded-v2/01_repeatable_frame.step'
G=9.81; KIT=.30; UPPER_TIER_PRINT=.15; E=1000.; NU=.35; RHO=1.24e-9

def run(h):
 out=ROOT/'results'/f'h{h:g}';out.mkdir(exist_ok=True)
 gmsh.initialize();gmsh.option.setNumber('General.Terminal',1)
 gmsh.model.add('actual_v2_frame');gmsh.model.occ.importShapes(str(SRC));gmsh.model.occ.synchronize()
 gmsh.option.setNumber('Mesh.MeshSizeMin',h*.35)
 gmsh.option.setNumber('Mesh.MeshSizeMax',h)
 gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',8)
 gmsh.option.setNumber('Mesh.Algorithm3D',1)
 gmsh.option.setNumber('Mesh.SecondOrderLinear',1);gmsh.model.mesh.generate(3);gmsh.model.mesh.setOrder(2)
 gmsh.write(str(out/'frame.msh'))
 nt,coords,_=gmsh.model.mesh.getNodes();coords=np.asarray(coords).reshape(-1,3);node={int(n):p for n,p in zip(nt,coords)}
 et,conn=gmsh.model.mesh.getElementsByType(11);con=np.asarray(conn).reshape(-1,10)
 # Gmsh tetra10 and Abaqus/CalculiX last two edges differ: gmsh 9=(3,4),10=(2,4); ccx vice versa
 con=con[:,[0,1,2,3,4,5,6,7,9,8]]
 # Consistent forces for uniform pressure on planar quadratic triangles: corner=0, midside=A/3
 force={};areas={'rail':0.,'post':0.};groups={'rail':[],'post':[]}
 for dim,s in gmsh.model.getEntities(2):
  b=gmsh.model.getBoundingBox(dim,s)
  if abs(b[5]-b[2])>1e-5:continue
  z=(b[2]+b[5])/2
  group=None
  if abs(z-8)<1e-5 and ((17.79<b[0]<17.81 and 22.39<b[3]<22.41) or (119.59<b[0]<119.61 and 124.19<b[3]<124.21)):group='rail'
  if abs(z-60)<1e-5:group='post'
  if not group:continue
  st,sc=gmsh.model.mesh.getElementsByType(9,s)
  for tri in np.asarray(sc).reshape(-1,6):
   pts=np.array([node[int(n)] for n in tri[:3]])
   a=float(np.linalg.norm(np.cross(pts[1]-pts[0],pts[2]-pts[0]))/2)
   areas[group]+=a;groups[group].append((tri,a))
 assert abs(areas['rail']-828)<.01,areas
 for group,F in [('rail',KIT*G),('post',2*(KIT+UPPER_TIER_PRINT)*G)]:
  for tri,a in groups[group]:
   for n in tri[3:]:force[int(n)]=force.get(int(n),0)-F*a/areas[group]/3
 supports=[int(n) for n,p in node.items() if abs(p[2])<1e-6 and (p[0]<16 or p[0]>126) and (p[1]<16 or p[1]>116)]
 anchor1=min(supports,key=lambda n:np.linalg.norm(node[n]-[8,2,0]));anchor2=min(supports,key=lambda n:np.linalg.norm(node[n]-[134,2,0]))
 volume=0.
 for e in con:
  p=np.array([node[int(n)] for n in e[:4]])
  volume+=abs(np.linalg.det((p[1:]-p[0]).T))/6
 meta={'h_mm':h,'nodes':len(nt),'elements_C3D10':len(et),'mesh_linearized_volume_mm3':volume,'cad_volume_mm3':108772.2855790358,'areas_mm2':areas,'supports_nodes':len(supports),'anchors':[anchor1,anchor2],'E_MPa':E,'poisson':NU,'density_tonne_mm3':RHO,'kit_mass_kg':KIT,'upper_tier_print_mass_kg':UPPER_TIER_PRINT,'applied_nodal_load_N':-sum(force.values()),'gravity_selfweight_N_linearized':volume*RHO*G*1000,'total_load_N_linearized':-sum(force.values())+volume*RHO*G*1000,'source_step_sha256':hashlib.sha256(SRC.read_bytes()).hexdigest(),'node_order_gmsh_to_ccx':[0,1,2,3,4,5,6,7,9,8]}
 (out/'metadata.json').write_text(json.dumps(meta,indent=2))
 with (out/'frame.inp').open('w') as f:
  f.write('*HEADING\nActual v2 frame, static screening only, gravity seated stack\n*NODE\n')
  for n,p in node.items():f.write(f'{n},'+','.join(f'{x:.12g}' for x in p)+'\n')
  f.write('*ELEMENT,TYPE=C3D10,ELSET=ALL\n')
  for e,c in zip(et,con):f.write(str(e)+','+','.join(map(str,c))+'\n')
  f.write('*NSET,NSET=SUPPORT\n')
  for i in range(0,len(supports),16):f.write(','.join(map(str,supports[i:i+16]))+'\n')
  f.write('*MATERIAL,NAME=EFFECTIVE_SCREEN\n*ELASTIC\n1000.,0.35\n*DENSITY\n1.24e-9\n*SOLID SECTION,ELSET=ALL,MATERIAL=EFFECTIVE_SCREEN\n*BOUNDARY\nSUPPORT,3,3,0\n')
  f.write(f'{anchor1},1,2,0\n{anchor2},2,2,0\n*STEP\n*STATIC\n*CLOAD\n')
  for n,v in force.items():f.write(f'{n},3,{v:.12g}\n')
  f.write('*DLOAD\nALL,GRAV,9810.,0.,0.,-1.\n*NODE FILE\nU,RF\n*EL FILE\nS\n*NODE PRINT,NSET=SUPPORT,TOTALS=YES\nRF\n*END STEP\n')
 np.savez_compressed(out/'mesh.npz',nodes=nt,coordinates=coords,elements=et,connectivity=con,supports=supports)
 gmsh.finalize()
 env=os.environ.copy();env['LD_LIBRARY_PATH']=str(ROOT/'tools/ccx/usr/lib/x86_64-linux-gnu');env['OMP_NUM_THREADS']='2';env['UCX_VFS_ENABLE']='n'
 with (out/'solver.log').open('w') as f:
  p=subprocess.run([str(ROOT/'tools/ccx/usr/bin/ccx'),'-i','frame'],cwd=out,env=env,stdout=f,stderr=subprocess.STDOUT)
 print('solver exit',p.returncode,'mesh',h,flush=True)
 if p.returncode!=0:raise RuntimeError('solver failed')
for h in [4.,2.8]:run(h)
