"""Actual STEP gravity screening, Gmsh4.13.1/CalculiX2.20; no contact/CFD."""
import sys,os,json,hashlib,subprocess,pathlib
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parent
DEP=pathlib.Path(os.environ.get('STUDY_DEPS',str(ROOT.parent/'jetson-pla-study/tools')))
sys.path.insert(0,str(DEP/'python'));import gmsh
A=json.loads((ROOT/'analysis_assumptions.json').read_text());G=9.81;RHO=A['density_tonne_mm3'];KIT=A['kit_mass_kg']
def run(kind,h):
 out=ROOT/'results'/f'{kind}-h{h:g}';out.mkdir(parents=True,exist_ok=True);SRC=ROOT/f'{kind}.step'
 gmsh.initialize();gmsh.model.add(A['design']+'_'+kind);gmsh.model.occ.importShapes(str(SRC));gmsh.model.occ.synchronize()
 vols=gmsh.model.getEntities(3);cadvol=sum(gmsh.model.occ.getMass(d,t) for d,t in vols)
 # Imprint pressure footprint and receiving-socket boundary without adding material.
 seatingfaces=gmsh.model.occ.importShapes(str(ROOT/'seating_contact.step'));gmsh.model.occ.synchronize();seatingfaces=[dt for dt in seatingfaces if dt[0]==2];assert len(seatingfaces)==4
 disks=list(seatingfaces)
 for poly in A['rail_pressure_polygons_mm']:
  points=[gmsh.model.occ.addPoint(float(x),float(y),8) for x,y,z in poly];lines=[gmsh.model.occ.addLine(points[i],points[(i+1)%len(points)]) for i in range(len(points))];disks.append((2,gmsh.model.occ.addPlaneSurface([gmsh.model.occ.addCurveLoop(lines)])))
 fragments,mapping=gmsh.model.occ.fragment(vols,disks);gmsh.model.occ.synchronize();upperpressuretags={t for group in mapping[len(vols):len(vols)+4] for d,t in group if d==2}
 assert abs(sum(gmsh.model.occ.getMass(d,t) for d,t in gmsh.model.getEntities(3))/cadvol-1)<1e-9,'CAD imprint added/lost volume'
 boundary=set(gmsh.model.getBoundary(gmsh.model.getEntities(3),combined=False,oriented=False,recursive=False));orphan=[dt for dt in gmsh.model.getEntities(2) if dt not in boundary]
 if orphan:gmsh.model.occ.remove(orphan,recursive=True);gmsh.model.occ.synchronize()
 gmsh.option.setNumber('Mesh.MeshSizeMin',h*.22);gmsh.option.setNumber('Mesh.MeshSizeMax',h);gmsh.option.setNumber('Mesh.MeshSizeFromCurvature',12);gmsh.option.setNumber('Mesh.Algorithm3D',1);gmsh.option.setNumber('Mesh.SecondOrderLinear',1)
 gmsh.model.mesh.generate(3);gmsh.model.mesh.setOrder(2);gmsh.write(str(out/f'{kind}.msh'))
 nt,coords,_=gmsh.model.mesh.getNodes();coords=np.asarray(coords).reshape(-1,3);node=dict(zip(map(int,nt),coords));et,conn=gmsh.model.mesh.getElementsByType(11);con=np.asarray(conn).reshape(-1,10)[:,[0,1,2,3,4,5,6,7,9,8]]
 q=np.asarray(gmsh.model.mesh.getElementQualities(et,'minDetJac'));gamma=np.asarray(gmsh.model.mesh.getElementQualities(et,'gamma'))
 assert np.all(q>0),'nonpositive Jacobian'
 groups={};supportset=set()
 from matplotlib.path import Path as PolyPath
 pressurepaths=[PolyPath(np.array(poly)[:,:2]) for poly in A['rail_pressure_polygons_mm']]
 def facegroup(b):
  if abs(b[5]-b[2])>1e-5:return None
  z=.5*(b[2]+b[5]);cx=.5*(b[0]+b[3]);cy=.5*(b[1]+b[4])
  if abs(z-8)<1e-5:return 'board'
  if abs(z-60)<1e-5:return None
  return None
 for dim,s in gmsh.model.getEntities(2):
  b=gmsh.model.getBoundingBox(dim,s);group='upper' if s in upperpressuretags else facegroup(b)
  if group:
   _,c=gmsh.model.mesh.getElementsByType(9,s)
   for tri in np.asarray(c).reshape(-1,6):
    p=np.array([node[int(n)] for n in tri[:3]])
    if group=='board' and not any(path.contains_point(p.mean(axis=0)[:2],radius=1e-6) for path in pressurepaths):continue
    area=float(np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))/2);groups.setdefault(group,[]).append((tri,area))
  if kind=='pin' and abs(b[2]-3)<1e-5 and abs(b[5]-3)<1e-5 and max(b[3]-b[0],b[4]-b[1])>3.4+1e-4:
   ids,_,_=gmsh.model.mesh.getNodes(2,s,includeBoundary=True);supportset.update(map(int,ids))
 if kind=='frame':
  size=A['bottom_support_corner_size_mm'];xs=[p[0] for p in A['frame_contact_centers_mm']];ys=[p[1] for p in A['frame_contact_centers_mm']]
  supportset={n for n,p in node.items() if abs(p[2])<1e-6 and any(abs(p[0]-x)<=size/2+1e-6 and abs(p[1]-y)<=size/2+1e-6 for x,y in A['frame_contact_centers_mm'])}
 supports=sorted(supportset);assert supports
 force={};areas={k:sum(a for t,a in ts) for k,ts in groups.items()}
 loads={'board':KIT*G if kind=='frame' else KIT*G/4}
 if kind=='frame':loads['upper']=2*(KIT+A['upper_tier_print_mass_kg'])*G
 for group,F in loads.items():
  assert group in groups,(group,areas)
  for tri,a in groups[group]:
   for n in tri[3:]:force[int(n)]=force.get(int(n),0)-F*a/areas[group]/3
 anchor_target=np.array([coords[:,0].min(),coords[:,1].min(),0]);anchor1=min(supports,key=lambda n:np.linalg.norm(node[n]-anchor_target));anchor2=max(supports,key=lambda n:node[n][0]);volume=0.
 for c in con:
  p=np.array([node[int(n)] for n in c[:4]]);volume+=abs(np.linalg.det((p[1:]-p[0]).T))/6
 solver='SPOOLES'
 meta={'linear_equation_solver':solver,'kind':kind,'h_mm':h,'nodes':len(nt),'elements_C3D10':len(et),'mesh_linearized_volume_mm3':volume,'cad_volume_mm3':cadvol,'areas_mm2':areas,'loads_N':loads,'support_nodes':len(supports),'anchors':[anchor1,anchor2],'E_MPa':A['E_MPa'],'density_tonne_mm3':RHO,'applied_nodal_load_N':-sum(force.values()),'gravity_selfweight_N_linearized':volume*RHO*G*1000,'total_load_N_linearized':-sum(force.values())+volume*RHO*G*1000,'source_step_sha256':hashlib.sha256(SRC.read_bytes()).hexdigest(),'seating_contact_sha256':hashlib.sha256((ROOT/'seating_contact.step').read_bytes()).hexdigest(),'exact_seating_CAD_area_mm2':523.3345078963703,'mesh_quality':{'min_determinant_Jacobian_mm3':float(q.min()),'min_gamma':float(gamma.min()),'nonpositive_Jacobian_elements':int((q<=0).sum())},'imprinted_planar_pressure_patches':True,'pressure_patch_basis':'polygons derived from actual stock guard bottom intersection with actual rails; curved-edge endpoints approximated by straight chords'}
 (out/'metadata.json').write_text(json.dumps(meta,indent=2))
 with (out/f'{kind}.inp').open('w') as f:
  f.write('*HEADING\n'+A['design']+' '+kind+' gravity seated screening\n*NODE\n')
  for n,p in node.items():f.write(f'{n},'+','.join(f'{x:.12g}' for x in p)+'\n')
  f.write('*ELEMENT,TYPE=C3D10,ELSET=ALL\n')
  for e,c in zip(et,con):f.write(str(e)+','+','.join(map(str,c))+'\n')
  f.write('*NSET,NSET=SUPPORT\n')
  for i in range(0,len(supports),16):f.write(','.join(map(str,supports[i:i+16]))+'\n')
  f.write(f'*MATERIAL,NAME=EFFECTIVE_SCREEN\n*ELASTIC\n{A["E_MPa"]},{A["nu"]}\n*DENSITY\n{RHO}\n*SOLID SECTION,ELSET=ALL,MATERIAL=EFFECTIVE_SCREEN\n*BOUNDARY\nSUPPORT,3,3,0\n{anchor1},1,2,0\n{anchor2},2,2,0\n*STEP\n*STATIC,SOLVER={solver}\n*CLOAD\n')
  for n,v in force.items():f.write(f'{n},3,{v:.12g}\n')
  f.write('*DLOAD\nALL,GRAV,9810.,0.,0.,-1.\n*NODE FILE\nU,RF\n*EL FILE\nS\n*NODE PRINT,NSET=SUPPORT,TOTALS=YES\nRF\n*END STEP\n')
 np.savez_compressed(out/'mesh.npz',nodes=nt,coordinates=coords,elements=et,connectivity=con,supports=supports);gmsh.finalize()
 env=os.environ.copy();env['LD_LIBRARY_PATH']=str(DEP/'ccx/usr/lib/x86_64-linux-gnu');env['OMP_NUM_THREADS']='2';env['UCX_VFS_ENABLE']='n'
 with (out/'solver.log').open('w') as f:p=subprocess.run([str(DEP/'ccx/usr/bin/ccx'),'-i',kind],cwd=out,env=env,stdout=f,stderr=subprocess.STDOUT)
 assert p.returncode==0,('solver failed',p.returncode);print('DONE',kind,h,meta,flush=True)
if __name__=='__main__':
 for kind in ['frame']:
  for h in A[kind+'_mesh_h_mm']:run(kind,h)
