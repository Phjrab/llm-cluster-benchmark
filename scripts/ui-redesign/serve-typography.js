// Static, loopback-only typography evidence server. No Controller or job imports.
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..');
const ref=process.env.TYPOGRAPHY_SOURCE_REF;
const sha=ref?execFileSync('git',['rev-parse','--verify',`${ref}^{commit}`],{cwd:root,encoding:'utf8'}).trim():null;
const port=Number(process.env.DASHBOARD_TEST_PORT||46178);
http.createServer((req,res)=> {
  const pathname=new URL(req.url,'http://127.0.0.1').pathname;
  if(req.method!=='GET' || (pathname!=='/' && !pathname.startsWith('/static/'))) {res.writeHead(404);return res.end();}
  const relative=pathname==='/'?'cluster/dashboard/templates/index.html':'cluster/dashboard'+pathname;
  const file=path.resolve(root,relative);
  if(!file.startsWith(path.join(root,'cluster/dashboard')+path.sep)) {res.writeHead(403);return res.end();}
  try {
    const bytes=sha?execFileSync('git',['show',`${sha}:${relative}`],{cwd:root,stdio:['ignore','pipe','ignore'],maxBuffer:16*1024*1024}):fs.readFileSync(file);
    res.setHeader('Content-Type',file.endsWith('.css')?'text/css':file.endsWith('.js')?'application/javascript':file.endsWith('.woff2')?'font/woff2':'text/html');res.end(bytes);
  } catch (_) {res.writeHead(404);res.end();}
}).listen(port,'127.0.0.1',()=>console.log(`Static typography source ${sha||'working tree'} on ${port}`));
