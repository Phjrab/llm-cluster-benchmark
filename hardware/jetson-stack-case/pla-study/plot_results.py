import pathlib,sys,numpy as np,json,os
R=pathlib.Path(__file__).resolve().parent;sys.path.insert(0,str(R/'tools/python'));import gmsh
os.environ['MPLCONFIGDIR']=str(R/'tmp/mpl')
import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
out=R/'results/h2.8';z=np.load(out/'mesh.npz');p=z['coordinates'];nd={n:i for i,n in enumerate(z['nodes'])};fld=np.load(out/'fields.npz')
gmsh.initialize();gmsh.open(str(out/'frame.msh'));_,tri=gmsh.model.mesh.getElementsByType(9);t=np.array(tri).reshape(-1,6)[:,:3];gmsh.finalize();t=np.array([[nd[n] for n in row] for row in t]);verts=p[t]
fig=plt.figure(figsize=(13,5.8),facecolor='white')
for j,(field,title,vmax,label) in enumerate([(np.linalg.norm(fld['displacement'],axis=1),'Static displacement',.0124,'Displacement [mm]'),(fld['von_mises'],'Continuum equivalent stress',.29,'von Mises [MPa]')]):
 ax=fig.add_subplot(1,2,j+1,projection='3d');norm=plt.Normalize(0,vmax);coll=Poly3DCollection(verts,facecolors=plt.cm.viridis(norm(field[t].mean(axis=1))),linewidths=0,rasterized=True);ax.add_collection3d(coll);ax.set_xlim(0,142);ax.set_ylim(0,132);ax.set_zlim(0,66);ax.set_box_aspect((142,132,66));ax.view_init(elev=25,azim=-57);ax.set_xlabel('X [mm]');ax.set_ylabel('Y [mm]');ax.set_zlabel('Z [mm]');ax.set_title(title,fontweight='bold');sm=plt.cm.ScalarMappable(norm=norm,cmap='viridis');fig.colorbar(sm,ax=ax,shrink=.62,pad=.1,label=label)
fig.suptitle('Jetsonstack v2 | actual STEP geometry | un-deformed shape',fontsize=15,y=.99)
fig.text(.5,.025,'CalculiX 2.20 / C3D10 / h=2.8 mm / effective E=1000 MPa / gravity only / 0.30 kg kit per tier\nHomogenized screening model: does not resolve 30% infill, layer failure, contact, heat or creep',ha='center',fontsize=9)
fig.subplots_adjust(left=.02,right=.97,top=.92,bottom=.12,wspace=.13);fig.savefig(R/'static_results.png',dpi=180);plt.close(fig)
# Flow energy balance sensitivity, not a temperature prediction
q=np.linspace(2,15,200);rho=1.18;cp=1005;cfm=.00047194745
fig,ax=plt.subplots(figsize=(7.4,4.4))
for watts in [45,75,90]:ax.plot(q,25+watts/(rho*cp*cfm*q),label=f'{watts} W total')
ax.axhline(45,color='grey',linestyle='--',label='45 C provisional assessment level');ax.set_ylim(25,95);ax.set_xlabel('Effective shared airflow [CFM], assumed');ax.set_ylabel('Mixed-air outlet temperature [C]');ax.set_title('Air energy-balance sensitivity | ambient 25 C');ax.grid(alpha=.2);ax.legend();fig.text(.5,.005,'Not CFD or PLA temperature: unknown real airflow, hotspots and conduction remain',ha='center',fontsize=9);fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(R/'airflow_sensitivity.png',dpi=160)
