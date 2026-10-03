import pathlib,json,numpy as np
R=pathlib.Path(__file__).resolve().parent;checks=[]
for path in sorted((R/'results').glob('*-h*/mesh.npz')):
 z=np.load(path);nodes=z['nodes'];parent={int(n):int(n) for n in nodes}
 def root(n):
  while parent[n]!=n:parent[n]=parent[parent[n]];n=parent[n]
  return n
 for c in z['connectivity']:
  a=root(int(c[0]))
  for n in c[1:]:
   b=root(int(n))
   if a!=b:parent[b]=a
 comp=len({root(n) for n in parent});m=json.loads((path.parent/'metadata.json').read_text());checks.append({'case':path.parent.name,'connected_mesh_components':comp,**m['mesh_quality'],'positive_Jacobian_passed':m['mesh_quality']['nonpositive_Jacobian_elements']==0,'support_nodes':len(z['supports'])});assert comp==1,checks[-1]
(R/'results/mesh_checks.json').write_text(json.dumps(checks,indent=2));print(checks)
