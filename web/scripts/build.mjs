import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dest = process.env.INVOICEBOX_OUTPUT_DIR ? path.resolve(process.env.INVOICEBOX_OUTPUT_DIR) : path.join(root, 'dist');
const modules = path.join(root, 'node_modules');
const baseline = await readFile(path.join(root, 'vendor/site-2026.11.05.1.js'));
const hash = createHash('sha256').update(baseline).digest('hex');
if (hash !== '0e3a116795ed38508ffadf9d344198a5b3b98febe9a845075cab81aec9bc2acd') {
  throw new Error('发布包校验失败，停止构建。请检查基线版本。');
}
let app = baseline.toString('utf8');
function replaceOnce(before, after) {
  const first = app.indexOf(before);
  if (first < 0 || app.indexOf(before, first + before.length) !== -1) {
    throw new Error(`无法安全定位构建补丁: ${before.slice(0, 90)}`);
  }
  app = app.slice(0, first) + after + app.slice(first + before.length);
}
function replaceExpected(before, after, count) {
  const found = app.split(before).length - 1;
  if (found !== count) throw new Error(`构建补丁数量不匹配: ${before.slice(0, 70)} (${found}/${count})`);
  app = app.split(before).join(after);
}
const start = app.indexOf('function gV(e){');
const end = app.indexOf('function _V(e){', start);
if (start < 0 || end < 0 || app.indexOf('function gV(e){', start + 1) !== -1) throw new Error('无法安全定位匹配函数');
app = app.slice(0, start) + 'function gV(e){return resolveAssociations(e)}' + app.slice(end);
// The production shortcut used MediaBox for ordinary invoices. Some providers
// place a landscape CropBox on a portrait MediaBox, causing a false 90° turn.
replaceOnce('if(hV(t)!==`expense`||t.category!==`机票`&&t.category!==`住宿`)return(await e.embedPdf(n,n.getPageIndices())).map(e=>({width:e.width,height:e.height,draw:(t,n)=>t.drawPage(e,n)}));', '');
replaceOnce('let a=r.map(e=>{let t=e.getCropBox(),n=e.getMediaBox(),r=Math.max(t.x,n.x),i=Math.max(t.y,n.y),a=Math.min(t.x+t.width,n.x+n.width),o=Math.min(t.y+t.height,n.y+n.height);return a>r&&o>i?{left:r,bottom:i,right:a,top:o}:{left:n.x,bottom:n.y,right:n.x+n.width,top:n.y+n.height}});', 'let a=r.map(visiblePageBox);');
replaceOnce('return(await e.embedPages(r,a)).map((e,t)=>{let n=(r[t].getRotation().angle%360+360)%360,i=n===90||n===270;return{width:i?e.height:e.width,height:i?e.width:e.height,sourceWidth:e.width,sourceHeight:e.height,rotation:n,draw:(t,n)=>t.drawPage(e,n)}})', 'return(await e.embedPages(r,a)).map((e,t)=>embeddedPageGeometry(e,r[t]))');
replaceOnce('async function MV(e,t,n=1200,r=`png`){', 'async function MV(e,t,n=1200,r=`png`,ibOptions={}){');
const ofdStart = app.indexOf('if(a===`ofd`){let t=await xB(i);', app.indexOf('async function MV('));
const ofdEnd = app.indexOf('return[await NV(e,i,a===`jpg`||a===`jpeg`)]}', ofdStart);
if (ofdStart < 0 || ofdEnd < 0) throw new Error('无法安全定位 OFD 渲染入口');
app = app.slice(0, ofdStart) + `if(a===\`ofd\`){
  const record=t, doc=await xB(i), pageCount=TB(doc), output=[];
  if(!pageCount)throw Error(\`OFD 文件没有可打印页面\`);
  for(let pageIndex=0;pageIndex<pageCount;pageIndex++){
    let pageDpi=n;
    if(ibOptions.adaptive)try{
      const page=doc.document.pages.page[pageIndex], xml=Kz(await doc.readFile(_B(doc.node.docBody.docRoot,page.baseLoc))), box=Qz.from(xml.area.physicalBox);
      const special=hV(record)===\`expense\`&&xV.has(record.category);
      pageDpi=sourceDpiForPrint(box.width,box.height,n,{special,preferPortrait:record.category===\`机票\`||record.category===\`住宿\`});
    }catch(error){console.warn(\`无法计算 OFD 页面尺寸，使用默认精度\`)}
    const key=record.hash?record.hash+':'+pageIndex+':'+pageDpi+':'+r:'';
    let blob=key?ofdBlobCache.get(key):undefined;
    if(!blob){
      const canvas=document.createElement(\`canvas\`);
      const render=async dpi=>{canvas.width=canvas.height=0;await wB(doc,pageIndex,canvas,{dpi});return await ofdCanvasBlob(canvas,r)};
      let fallback=false;
      try{
        blob=await render(pageDpi);
        if(!blob&&pageDpi>600){fallback=true;console.warn(\`OFD 页面生成失败，回退到 600 DPI\`);blob=await render(600)}
        if(!blob)throw Error(\`OFD 页面生成失败，请重试\`);
        if(key&&!fallback)ofdBlobCache.set(key,blob);
      }finally{canvas.width=canvas.height=0}
    }
    output.push(await NV(e,blob,r===\`jpeg\`));
  }
  return output;
}` + app.slice(ofdEnd);
replaceOnce('let o=await oT.create(),s=[];', 'let o=await oT.create(),s=[];if(r.report)await appendReport(o,r.report,{render:LV.default,duplex:sx.Simplex,printScaling:ox.None});');
replaceOnce('let d=l.groups.map(e=>[e.primary,...e.attachments]);', 'let ibProcessed=0,ibTotal=e.length,d=l.groups.map(e=>[e.primary,...e.attachments]);');
replaceOnce('for(let n of e){let e=await MV(o,n,i,a);', 'for(let n of e){if(r.report){let p=document.querySelector(`#print-overlay span`);p&&(p.textContent=`正在生成 PDF（${++ibProcessed}/${ibTotal}）`)}let e=await MV(o,n,i,a,{adaptive:r.adaptiveOfd===!0});');
replaceOnce('function yV(e){', 'function ibOriginalIssues(e){');
replaceOnce('Dk.workerSrc=uj;', `function yV(e){
  const issues=ibOriginalIssues(e), bundle=_V(e);
  for(const {primary,attachments} of bundle.groups){
    for(const item of attachments){
      if(item.association_warning) issues.push({kind:'association_conflict',ids:[item.id],message:(item.display_name||'附件')+'的手动关联存在'+item.association_warning+'，请核对原件。',key:JSON.stringify(['association_conflict',item.id,primary.id,item.association_warning,item.amount,primary.amount])});
    }
  }
  return issues;
}Dk.workerSrc=uj;`);
replaceOnce('id:a,display_name:e.name,name:a.slice', 'id:a,original_name:e.name,display_name:e.name,name:a.slice');
replaceOnce('[`display_name`,`category`,`amount`,`document_role`,`parent_id`,`invoice_number`,`amount_confirmed`]', '[`display_name`,`category`,`amount`,`document_role`,`parent_id`,`association_origin`,`invoice_number`,`amount_confirmed`]');
replaceOnce('if(hV(n)===`expense`)n.parent_id=``;', 'if(hV(e)!==hV(n)){n.parent_id=``;n.association_origin=`auto`}if(hV(n)===`expense`)n.parent_id=``;');
replaceOnce('await Rg({...t,parent_id:``});return await Rg(n)', 'await Rg({...t,parent_id:``,association_origin:`auto`});return await Rg(n)');
replaceOnce('await Rg({...e,parent_id:``});return zV({ok:!0})', 'await Rg({...e,parent_id:``,association_origin:`auto`});return zV({ok:!0})');
replaceOnce('if(n===`/api/delete`){await zg(r.id);', 'if(n===`/api/delete`){let entry=i.find(e=>e.id===r.id);entry?.hash&&ofdBlobCache.deletePrefix(entry.hash+`:`);await zg(r.id);');
replaceOnce('if(n===`/api/clear`){VV++', 'if(n===`/api/clear`){ofdBlobCache.clear();VV++');
replaceOnce('n===`/api/render_pages`?{dpi:300}:{dpi:1200,imageFormat:`jpeg`}', 'n===`/api/render_pages`?{dpi:300}:{dpi:Number(r.ofd_dpi)===1200?1200:600,imageFormat:`jpeg`,adaptiveOfd:!0,report:document.querySelector(`.report-paper`)}');
const reportStart = app.indexOf('async function UV(');
const downloadStart = app.indexOf('async function WV(', reportStart);
if(reportStart < 0 || downloadStart < 0)throw new Error('无法安全定位 PDF 报销单拼接入口');
app = app.slice(0,reportStart) + app.slice(downloadStart);
replaceOnce('async function WV(e,t){try{let n=URL.createObjectURL(await UV(e,t)),r=document.createElement(`a`);r.href=n,r.download=`发票与报销单.pdf`,r.click(),setTimeout(()=>URL.revokeObjectURL(n),6e4)}finally{URL.revokeObjectURL(e)}}', 'async function WV(e){try{let r=document.createElement(`a`);r.href=e,r.download=`发票与报销单.pdf`,r.click(),setTimeout(()=>URL.revokeObjectURL(e),6e4)}catch(error){URL.revokeObjectURL(e);throw error}}');
replaceOnce('r=n.url;let a=await UV(r,document.querySelector(`.report-paper`));if(e.closed)return;let o=URL.createObjectURL(a);e.location.replace(o);', 'r=n.url;if(e.closed)return;let o=r;e.location.replace(o);r=void 0;');
replaceExpected('JSON.stringify({ids:O.value.map(e=>e.id),duplicate_special:m.value})', 'JSON.stringify({ids:O.value.map(e=>e.id),duplicate_special:m.value,ofd_dpi:ibOfdDpi.value})', 3);
replaceOnce('let z=rl(()=>{let e=[...O.value];', 'let z=rl(()=>{let e=[...k.value.ordered];');
replaceOnce('fe=e=>s.value=e,pe=e=>{if(s.value===e)return;let t=n.value.findIndex(e=>e.id===s.value),r=n.value.findIndex(t=>t.id===e);if(t<0||r<0)return;let i=n.value[t];n.value.splice(t,1),n.value.splice(r,0,i)},me=()=>{s.value=-1,R()}', 'fe=(id,event)=>{s.value=id;ibDragHover.value="";if(event?.dataTransfer){event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",id)}},pe=id=>{ibDragHover.value=id},me=()=>{s.value=-1;ibDragHover.value=""}');
replaceOnce('return Ha(()=>{N(),document.addEventListener', `let ibOfdDpi=Dn(600),ibDragHover=Dn(''),ibAssociationOpen=Dn(''),ibToggleAssociation=id=>{ibAssociationOpen.value=ibAssociationOpen.value===id?'':id},ibSetAssociation=async(item,parent)=>{try{await te({...item,parent_id:parent,association_origin:parent?'manual':'rejected'});await N()}catch(error){console.error(error)}},ibConfirmAssociation=async item=>ibSetAssociation(item,item.parent_id),ibRetryAssociation=async item=>{try{await te({...item,parent_id:'',association_origin:'auto'});await N()}catch(error){console.error(error)}},ibDropOnCard=async targetId=>{let plan=planCardDrop(n.value,s.value,targetId);s.value=-1;ibDragHover.value='';if(!plan)return;n.value=plan.records;if(d.value!=='default')d.value='default';try{if(plan.attachmentId){let idx=n.value.findIndex(item=>item.id===plan.attachmentId),updated={...n.value[idx],parent_id:plan.expenseId,association_origin:'manual'};n.value.splice(idx,1,updated);await te(updated)}await R();await N()}catch(error){console.error(error);await N()}};return Ha(()=>{N(),document.addEventListener`);
replaceOnce('issues:j,documentRole:hV,changeCategory:V', 'issues:j,documentRole:hV,ofdDpi:ibOfdDpi,reloadInvoices:N,setAssociation:ibSetAssociation,confirmAssociation:ibConfirmAssociation,retryAssociation:ibRetryAssociation,associationOpen:ibAssociationOpen,toggleAssociation:ibToggleAssociation,dragHover:ibDragHover,dropOnCard:ibDropOnCard,changeCategory:V');
replaceOnce('let e=`2026.11.05.1`,t=document.querySelector', 'let e=`2026.11.10.1`,t=document.querySelector');
app = `import { resolveAssociations } from './matching.js';\nimport { planCardDrop } from './drag-association.js';\nimport { visiblePageBox, embeddedPageGeometry } from './pdf-page.js';\nimport { sourceDpiForPrint, ofdBlobCache, ofdCanvasBlob } from './ofd-output.js';\nimport { appendReport } from './report-pdf.js';\nwindow.addEventListener('pagehide',()=>ofdBlobCache.clear());\n` + app;

await mkdir(path.join(dest, 'assets'), { recursive: true });
await cp(path.join(root, 'public'), dest, { recursive: true });
await cp(path.join(root, 'index.html'), path.join(dest, 'index.html'));
await cp(path.join(root, 'src/matching.js'), path.join(dest, 'assets/matching.js'));
await cp(path.join(root, 'src/drag-association.js'), path.join(dest, 'assets/drag-association.js'));
await cp(path.join(root, 'src/pdf-page.js'), path.join(dest, 'assets/pdf-page.js'));
await cp(path.join(root, 'src/ofd-output.js'), path.join(dest, 'assets/ofd-output.js'));
await cp(path.join(root, 'src/report-pdf.js'), path.join(dest, 'assets/report-pdf.js'));
await cp(path.join(root, 'src/association.css'), path.join(dest, 'assets/association.css'));
await writeFile(path.join(dest, 'assets/app.js'), app);
for (const part of ['cmaps', 'standard_fonts']) {
  await cp(path.join(modules, 'pdfjs-dist', part), path.join(dest, 'pdfjs', part), { recursive: true });
}
await mkdir(path.join(dest, 'ocr/core'), { recursive: true });
await mkdir(path.join(dest, 'ocr/lang'), { recursive: true });
await cp(path.join(modules, 'tesseract.js/dist/worker.min.js'), path.join(dest, 'ocr/worker.min.js'));
for (const name of await readdir(path.join(modules, 'tesseract.js-core'))) {
  if (name.endsWith('.wasm.js')) await cp(path.join(modules, 'tesseract.js-core', name), path.join(dest, 'ocr/core', name));
}
for (const lang of ['chi_sim', 'eng']) {
  await cp(path.join(modules, '@tesseract.js-data', lang, '4.0.0_best_int', `${lang}.traineddata.gz`), path.join(dest, 'ocr/lang', `${lang}.traineddata.gz`));
}
await mkdir(path.join(dest, 'licenses'), { recursive: true });
await cp(path.join(modules, 'pdfjs-dist/LICENSE'), path.join(dest, 'licenses/pdfjs-dist-LICENSE.txt'));
await cp(path.join(modules, 'tesseract.js/LICENSE.md'), path.join(dest, 'licenses/tesseract.js-LICENSE.txt'));
await cp(path.join(modules, 'tesseract.js-core/LICENSE'), path.join(dest, 'licenses/tesseract.js-core-LICENSE.txt'));
console.log(`本地网站已构建至 ${dest}`);
