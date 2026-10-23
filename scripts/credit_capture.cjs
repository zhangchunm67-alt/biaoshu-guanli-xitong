#!/usr/bin/env node
'use strict';

// Official-page automation only. No CAPTCHA solver, proxy rotation, stealth,
// hidden data endpoints, or inferred "no records" from an empty/error page.
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');

const SOURCES = Object.freeze([
  { id:'credit_dishonest', name:'失信被执行人', url:'https://zxgk.court.gov.cn/shixin/', scope:null },
  { id:'credit_tax_violation', name:'重大税收违法失信主体', url:'https://www.creditchina.gov.cn/zhuanxiangchaxun/zhongdashuishouweifaanjian/', scope:'#taxCheck' },
  { id:'credit_procurement_violation', name:'政府采购严重违法失信行为记录名单', url:'https://www.creditchina.gov.cn/zhuanxiangchaxun/zhengfucaigouyanzhongweifashixinmingdan/', scope:'#govPurchaseCheck' },
]);
// None of the three live result contracts could be verified: court WAF denied
// access, and both Credit China forms required CAPTCHA. Keep success disabled
// until a developer validates real zero/nonzero result DOM + request completion.
// This is a code-level adapter gate, not a user-editable preference.
const VERIFIED_RESULT_ADAPTERS = new Set();

const normalize = s => String(s || '').replace(/\s+/g,'').replace(/[（]/g,'(').replace(/[）]/g,')');
const stamp = () => new Date().toISOString();

function initial(source) {
  return {...source, scope:undefined, final_url:source.url, status:'unverified', reason:'尚未完成查询',
    query_completed:false, subject_verified:false, result_verified:false, screenshot_verified:false,
    screenshot:null, sha256:null};
}

function exactSource(actual, source) {
  try { const a = new URL(actual), b = new URL(source.url); return a.origin === b.origin && a.pathname.replace(/\/+$/,'') === b.pathname.replace(/\/+$/,''); }
  catch { return false; }
}

function blockReason(snapshot) {
  if (snapshot.challenge) return '官网要求验证码或人工安全验证，未执行人工接力';
  if (snapshot.http_status === 403 || snapshot.http_status === 412 || /403 Forbidden|Access Denied|GeoBL|访问被拒绝|访问过于频繁|访问受限|安全验证|人机验证/i.test(snapshot.body || ''))
    return '官网限制当前访问，无法确认查询结果';
  if (snapshot.http_status >= 400) return `官网返回 HTTP ${snapshot.http_status}，未完成查询`;
  return null;
}

// Classification is deliberately conservative. An empty table, timeout, generic
// "查询结果" heading, or HTTP 200 alone can never pass this gate.
function classify(snapshot, subject, source) {
  const base = initial(source), reason = blockReason(snapshot);
  base.final_url = snapshot.url || source.url;
  if (reason) return {...base, status:'blocked', reason};
  if (!exactSource(snapshot.url,source)) return {...base, reason:'页面跳转到未核验的地址，未采信结果'};
  if (!snapshot.submitted || normalize(snapshot.query_value) !== normalize(subject))
    return {...base, reason:'未验证本次提交的查询主体'};
  if (source.id === 'credit_dishonest' && snapshot.province !== '全部')
    return {...base, reason:'未验证查询省份为全部'};
  const text = String(snapshot.result_text || '').trim();
  if (!snapshot.result_scope_verified || !snapshot.result_transition_verified || !/查询结果|查询信息|查询列表/.test(text))
    return {...base, reason:'未识别到独立的官方查询结果区域'};
  const noRecord = /(?:未查询到(?:相关)?(?:信息|记录|结果)|未查到(?:相关)?(?:信息|记录|结果)|没有(?:符合条件的|相关)?(?:信息|记录|结果)|暂无(?:相关)?(?:信息|记录|结果)|共\s*0\s*条(?:记录|信息)?)/.test(text);
  const rows = (snapshot.result_rows || []).filter(Boolean);
  const matchingRows = rows.filter(s => normalize(s).includes(normalize(subject)));
  if (noRecord && rows.length === 0)
    return {...base, status:'no_records', reason:'官方结果区域明确显示本次查询未发现记录', query_completed:true, subject_verified:true, result_verified:true};
  if (matchingRows.length && !noRecord)
    return {...base, status:'records_found', reason:'官方结果包含同名主体记录，不能作为无记录材料自动归档', query_completed:true, subject_verified:true, result_verified:true};
  return {...base, reason:'结果为空、信息不一致或页面结构未验证，不能判断为无记录'};
}

function loadBrowserLibrary() {
  try { return require('playwright'); } catch {}
  // Reuse the official Codex bundled runtime when available; no download here.
  const bundled = path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
  try { return createRequire(bundled)('playwright'); } catch {}
  throw new Error('未找到 Playwright。请在代码目录安装 package.json 中的本地依赖。');
}

async function launchBrowser() {
  const {chromium} = loadBrowserLibrary();
  const candidates = process.platform === 'darwin' ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] : process.platform === 'win32' ? [
    path.join(process.env.PROGRAMFILES || '', 'Google/Chrome/Application/chrome.exe'),
    path.join(process.env['PROGRAMFILES(X86)'] || '', 'Microsoft/Edge/Application/msedge.exe'),
  ] : ['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'];
  let executablePath;
  for (const p of candidates) { try { await fs.access(p); executablePath=p; break; } catch {} }
  return chromium.launch({headless:true, ...(executablePath ? {executablePath}:{}), timeout:15000});
}

async function hasChallenge(page) {
  // Only inspect visibility; do not read, solve, submit, or interact with CAPTCHA.
  const inputs = page.locator('input');
  for (const input of await inputs.all()) {
    if (!await input.isVisible()) continue;
    const descriptor = [await input.getAttribute('id'), await input.getAttribute('name'), await input.getAttribute('placeholder'), await input.getAttribute('aria-label')].join(' ');
    if (/vcode|captcha|验证码|校验码/i.test(descriptor)) return true;
  }
  for (const f of page.frames()) {
    if (/captcha|recaptcha|hcaptcha/i.test(f.url())) return true;
  }
  return false;
}

async function snapshot(page, source, submitted=false, httpStatus=200, province='') {
  const body = await page.locator('body').innerText({timeout:2500});
  let queryValue='', resultText='', resultRows=[], resultScopeVerified=false;
  if (source.scope && await page.locator(source.scope).count() === 1) {
    const scope = page.locator(source.scope);
    const input = scope.getByPlaceholder('请输入主体名称或者统一社会信用代码',{exact:true});
    if (await input.count() === 1) queryValue=await input.inputValue();
    resultText=await scope.innerText();
    const tables=scope.locator('table');
    if (await tables.count() === 1) {
      resultScopeVerified=VERIFIED_RESULT_ADAPTERS.has(source.id);
      resultRows=await tables.locator('tbody tr').evaluateAll(rows=>rows.filter(r=>r.getBoundingClientRect().width>0 && r.querySelectorAll('td').length>1).map(r=>r.innerText.trim()));
    }
  } else if (!source.scope) {
    const input=page.getByLabel(/被执行人姓名.*名称|被执行人姓名\/名称/);
    if (await input.count() === 1) queryValue=await input.inputValue();
    // Court UI was blocked during live verification. Its result adapter remains
    // unverified, so even a future accessible page cannot auto-enable silently.
    resultText=''; resultScopeVerified=false;
  }
  return {url:page.url(), body, challenge:await hasChallenge(page), http_status:httpStatus,
    submitted, query_value:queryValue, result_text:resultText, result_rows:resultRows,
    result_scope_verified:resultScopeVerified, province};
}

async function submitQuery(page, source, subject) {
  if (!source.scope) {
    // No verified court form/result structure is available under current access
    // restrictions. Fail closed instead of guessing endpoints or bypassing WAF.
    return {submitted:false, province:'', unsupported:true};
  }
  const scope=page.locator(source.scope);
  await scope.waitFor({state:'visible',timeout:7000});
  const beforeResultText=await scope.innerText();
  const input=scope.getByPlaceholder('请输入主体名称或者统一社会信用代码',{exact:true});
  if (await input.count() !== 1) throw new Error('官方主体输入框发生变化');
  await input.fill(subject);
  const submit=scope.getByText('查 询',{exact:true});
  if (await submit.count() !== 1) throw new Error('官方查询按钮发生变化');
  await submit.click({timeout:5000});
  return {submitted:true, province:'', unsupported:false, beforeResultText};
}

async function captureSource(context, source, subject, outputDir) {
  const page=await context.newPage();
  let result=initial(source), httpStatus=200;
  const sourceDeadline=setTimeout(()=>page.close().catch(()=>{}),27000);
  try {
    const response=await page.goto(source.url,{waitUntil:'domcontentloaded',timeout:15000});
    httpStatus=response?.status() || 200;
    let state=await snapshot(page,source,false,httpStatus);
    const blocked=blockReason(state);
    if (blocked) result={...initial(source),status:'blocked',reason:blocked,final_url:page.url()};
    else if (!exactSource(page.url(),source)) result={...initial(source),reason:'页面跳转到未核验地址，未提交主体',final_url:page.url()};
    else {
      const query=await submitQuery(page,source,subject);
      if (query.unsupported) result={...initial(source),reason:'法院查询结果结构尚未完成实站验证，保持停用'};
      else {
        const until=Date.now()+9000;
        let stableText='', stableSince=Date.now();
        do {
          state=await snapshot(page,source,true,httpStatus,query.province);
          if (state.result_text !== stableText) {stableText=state.result_text;stableSince=Date.now();}
          state.result_transition_verified=state.result_text !== query.beforeResultText && Date.now()-stableSince >= 1200;
          result=classify(state,subject,source);
          if (['blocked','no_records','records_found'].includes(result.status)) break;
          await page.waitForTimeout(400);
        } while(Date.now()<until);
        if (result.status==='unverified' && !VERIFIED_RESULT_ADAPTERS.has(source.id)) result.reason='该官网的成功结果结构尚未通过实站验证，保持停用；空白或旧结果不能作为无记录证明';
      }
    }
    // Diagnostic captures remain under credit_checks. Only a fully verified set
    // can be imported by the server; an error/CAPTCHA screenshot is never material.
    if (!page.isClosed()) {
      const filename=source.id+'.png';
      const bytes=await page.screenshot({fullPage:true,timeout:5000});
      await fs.writeFile(path.join(outputDir,filename),bytes,{mode:0o600});
      result.screenshot=filename;
      result.sha256=crypto.createHash('sha256').update(bytes).digest('hex');
      const after=await snapshot(page,source,result.query_completed,httpStatus);
      const afterResult=classify({...after,result_transition_verified:true},subject,source);
      const unchanged=after.result_text===state.result_text && JSON.stringify(after.result_rows)===JSON.stringify(state.result_rows);
      result.screenshot_verified=result.query_completed && result.subject_verified && result.result_verified && unchanged && afterResult.status===result.status && afterResult.result_verified;
      if (result.query_completed && !result.screenshot_verified) result={...result,status:'unverified',reason:'截图时页面状态发生变化，未采信结果',query_completed:false,result_verified:false};
    }
  } catch (e) {
    result={...initial(source),status:'error',reason:page.isClosed()?'官网查询超时，未取得可确认结果':'官网页面未能加载或结构不符合已验证规则，未取得查询证明'};
  } finally {
    clearTimeout(sourceDeadline); await page.close().catch(()=>{});
  }
  result.checked_at=stamp(); return result;
}

function parseArgs(argv) {
  const args={};
  for(let i=0;i<argv.length;i+=2) {
    if (!['--subject','--output-dir'].includes(argv[i]) || !argv[i+1] || argv[i+1].startsWith('--') || args[argv[i]]) throw new Error('参数应为 --subject 公司全称 --output-dir 输出目录');
    args[argv[i]]=argv[i+1];
  }
  const subject=String(args['--subject'] || '').trim();
  if (!subject || subject.length>200 || /[\u0000-\u001f]/.test(subject)) throw new Error('请提供有效的公司全称');
  if (!args['--output-dir'] || !path.isAbsolute(args['--output-dir'])) throw new Error('输出目录必须是绝对路径');
  return {subject,outputDir:path.resolve(args['--output-dir'])};
}

async function main() {
  const {subject,outputDir}=parseArgs(process.argv.slice(2));
  await fs.mkdir(outputDir,{recursive:true,mode:0o700});
  if ((await fs.readdir(outputDir)).length) throw new Error('输出目录必须为空，请为每次查询使用新的目录，避免混入历史截图');
  const report={schema_version:1,subject,started_at:stamp(),finished_at:null,sources:[],all_verified:false,adapter_version:'2026-10-16.1'};
  let browser;
  try {
    browser=await launchBrowser();
    const context=await browser.newContext({viewport:{width:1440,height:1050},locale:'zh-CN',acceptDownloads:false});
    // No external submissions beyond the fixed official query pages; no uploads.
    for (const source of SOURCES) report.sources.push(await captureSource(context,source,subject,outputDir));
  } catch(e) {
    const reason=/Playwright/.test(e.message)?e.message:'本地查询浏览器无法启动，请检查 Chrome 或浏览器依赖';
    for (const source of SOURCES) if (!report.sources.some(s=>s.id===source.id)) report.sources.push({...initial(source),status:'error',reason});
  } finally { if(browser) await browser.close().catch(()=>{}); }
  report.finished_at=stamp();
  report.all_verified=report.sources.length===3 && report.sources.every(s=>s.status==='no_records' && s.query_completed && s.subject_verified && s.result_verified && s.screenshot_verified);
  await fs.writeFile(path.join(outputDir,'report.json'),JSON.stringify(report,null,2),{mode:0o600});
  process.stdout.write(JSON.stringify(report)+'\n');
}

module.exports={SOURCES,normalize,classify,exactSource,blockReason,parseArgs};
if(require.main === module) main().catch(e=>{process.stderr.write(e.message+'\n');process.exitCode=1;});
