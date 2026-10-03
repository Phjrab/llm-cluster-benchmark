import sys,pathlib,os,subprocess,json
import numpy as np
R=pathlib.Path(__file__).resolve().parent;DEP=pathlib.Path(os.environ.get('STUDY_DEPS',str(R.parent/'jetson-pla-study/tools')));sys.path.insert(0,str(DEP/'python'));import gmsh
out=R/'results/benchmark';out.mkdir(exist_ok=True)
gmsh.initialize();gmsh.model.add('compression_patch');gmsh.model.occ.addBox(0,0,0,10,10,10);gmsh.model.occ.synchronize();gmsh.option.setNumber('Mesh.MeshSizeMax',3);gmsh.option.setNumber('Mesh.MeshSizeMin',3);gmsh.option.setNumber('Mesh.SecondOrderLinear',1);gmsh.model.mesh.generate(3);gmsh.model.mesh.setOrder(2)
nt,p,_=gmsh.model.mesh.getNodes();p=np.array(p).reshape(-1,3);nodes=dict(zip(map(int,nt),p));et,c=gmsh.model.mesh.getElementsByType(11);c=np.array(c).reshape(-1,10)[:,[0,1,2,3,4,5,6,7,9,8]]
forces={}
for dim,s in gmsh.model.getEntities(2):
 b=gmsh.model.getBoundingBox(dim,s)
 if abs(b[2]-10)>1e-6 or abs(b[5]-10)>1e-6:continue
 _,tri=gmsh.model.mesh.getElementsByType(9,s)
 for t in np.array(tri).reshape(-1,6):
  q=np.array([nodes[int(n)] for n in t[:3]]);a=np.linalg.norm(np.cross(q[1]-q[0],q[2]-q[0]))/2
  for n in t[3:]:forces[int(n)]=forces.get(int(n),0)-.1*a/3
bottom=[n for n,p in nodes.items() if abs(p[2])<1e-6];a=min(bottom,key=lambda n:np.linalg.norm(nodes[n]));b=min(bottom,key=lambda n:np.linalg.norm(nodes[n]-[10,0,0]))
with (out/'patch.inp').open('w') as f:
 f.write('*NODE\n')
 for n,p in nodes.items():f.write(str(n)+','+','.join(map(str,p))+'\n')
 f.write('*ELEMENT,TYPE=C3D10,ELSET=ALL\n')
 for e,t in zip(et,c):f.write(str(e)+','+','.join(map(str,t))+'\n')
 f.write('*MATERIAL,NAME=PLA\n*ELASTIC\n1000.,0.35\n*SOLID SECTION,ELSET=ALL,MATERIAL=PLA\n*BOUNDARY\n')
 for n in bottom:f.write(f'{n},3,3,0\n')
 f.write(f'{a},1,2,0\n{b},2,2,0\n*STEP\n*STATIC\n*CLOAD\n')
 for n,v in forces.items():f.write(f'{n},3,{v}\n')
 f.write('*NODE FILE\nU,RF\n*EL FILE\nS\n*END STEP\n')
gmsh.finalize();env=os.environ.copy();env['LD_LIBRARY_PATH']=str(DEP/'ccx/usr/lib/x86_64-linux-gnu');env['OMP_NUM_THREADS']='2';env['UCX_VFS_ENABLE']='n'
with (out/'solver.log').open('w') as f:p=subprocess.run([str(DEP/'ccx/usr/bin/ccx'),'-i','patch'],cwd=out,env=env,stdout=f,stderr=subprocess.STDOUT)
(out/'expected.json').write_text(json.dumps({'solver_exit':p.returncode,'load_N':sum(forces.values()),'expected_uz_top_mm':-.001,'expected_szz_MPa':-.1,'expected_reaction_z_N':10},indent=2))
print(p.returncode)
