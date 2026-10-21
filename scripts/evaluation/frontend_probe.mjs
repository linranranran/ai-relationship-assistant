// 加载真实前端 SSE 订阅模块；只替换传输，精确重放断线/重复事件。
// 这是 adapter 级检查，不冒充真实浏览器网络或 React 卡片渲染检查。
import { readFile, writeFile } from 'node:fs/promises'
import vm from 'node:vm'
import path from 'node:path'

const root = process.cwd()
const source = await readFile(path.join(root, 'frontend/src/api/chat.js'), 'utf8')
let options
let posts = 0
const context = vm.createContext({localStorage:{getItem:()=>null},crypto:{randomUUID:()=> 'synthetic-id'}, window:{location:{}}})
const module = new vm.SourceTextModule(source,{context,initializeImportMeta:meta=>{meta.env={}}})
await module.link(async specifier=>{
  const exports = specifier === './client'
    ? {default:{defaults:{baseURL:'http://synthetic.local/api'},post:()=>{posts++}}}
    : specifier === '@microsoft/fetch-event-source'
      ? {fetchEventSource:async (_url,opts)=>{options=opts}}
      : {buildChatPayload:()=>({})}
  return new vm.SyntheticModule(Object.keys(exports), function(){
    for(const [name,value] of Object.entries(exports)) this.setExport(name,value)
  }, {context})
})
await module.evaluate()
const pending = {requestId:'request-evaluation',cursor:0}
const delivered = []
await module.namespace.subscribeRequest(pending,undefined,event=>{delivered.push(event);return false},()=>{})
const emit = (seq,request='request-evaluation',version=1)=>options.onmessage({event:'agent',
  data:JSON.stringify({schema_version:version,request_id:request,seq,type:'response.delta',text:'虚构片段'})})
emit(1);emit(1);emit(2);emit(2);emit(3,'another-request');emit(3,'request-evaluation',99)
const result = {case_id:'A04',layer:'production_frontend_adapter_stub_transport',
  pass:delivered.length===2 && pending.cursor===2 && posts===0,
  delivered_sequences:delivered.map(e=>e.seq),cursor:pending.cursor,extra_posts:posts,
  limitation:'transport stub; actual disconnect/reconnect separately tested via real SSE'}
await writeFile(path.join(root,'docs/评测文档/runs/integration-20261014/frontend-adapter-result.json'),JSON.stringify(result,null,2))
console.log(JSON.stringify(result))
if(!result.pass) process.exitCode=1
