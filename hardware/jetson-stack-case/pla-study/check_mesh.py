import sys,pathlib,json,numpy as np
R=pathlib.Path(__file__).resolve().parent;sys.path.insert(0,str(R/'tools/python'));import gmsh
checks=[]
for h in [4,2.8]:
 gmsh.initialize();gmsh.open(str(R/f'results/h{h:g}/frame.msh'));t,_=gmsh.model.mesh.getElementsByType(11);q=np.array(gmsh.model.mesh.getElementQualities(t,'minDetJac'));gamma=np.array(gmsh.model.mesh.getElementQualities(t,'gamma'));gmsh.finalize();checks.append({'h_mm':h,'min_determinant_Jacobian_mm3':float(q.min()),'min_gamma_quality':float(gamma.min()),'nonpositive_Jacobian_elements':int((q<=0).sum()),'passed':bool((q>0).all())})
(R/'results/mesh_quality.json').write_text(json.dumps(checks,indent=2));assert all(c['passed'] for c in checks);print(checks)
