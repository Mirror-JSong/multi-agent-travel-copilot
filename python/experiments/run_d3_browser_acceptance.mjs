/** Real Chromium/Edge acceptance flow over the running Streamlit application.
 *
 * Requires an already running browser with --remote-debugging-port.  It drives
 * the actual Streamlit DOM through Chrome DevTools Protocol and saves screenshots;
 * it does not inject fixture HTML or bypass the FastAPI calls made by the UI.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const args = Object.fromEntries(process.argv.slice(2).map((item, index, all) => {
  if (!item.startsWith("--")) return [null, null];
  return [item.slice(2), all[index + 1]];
}).filter(([key]) => key));
const debugBase = args.debug || "http://127.0.0.1:9233";
const appUrl = args.app || "http://127.0.0.1:8766";
const output = path.resolve(args.output || "experiments/results/d3_browser_acceptance.json");
const screenshotDir = path.resolve(args.screenshots || "../docs/assets/stage-d3");
fs.mkdirSync(path.dirname(output), {recursive: true});
fs.mkdirSync(screenshotDir, {recursive: true});

const targetResponse = await fetch(`${debugBase}/json/new?${encodeURIComponent(appUrl)}`, {method: "PUT"});
if (!targetResponse.ok) throw new Error(`cannot create browser target: ${targetResponse.status}`);
const target = await targetResponse.json();
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve, {once: true});
  socket.addEventListener("error", reject, {once: true});
});

let nextId = 1;
const pending = new Map();
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (!message.id || !pending.has(message.id)) return;
  const {resolve, reject} = pending.get(message.id);
  pending.delete(message.id);
  if (message.error) reject(new Error(JSON.stringify(message.error)));
  else resolve(message.result || {});
});
function call(method, params = {}) {
  const id = nextId++;
  socket.send(JSON.stringify({id, method, params}));
  return new Promise((resolve, reject) => pending.set(id, {resolve, reject}));
}
async function evaluate(expression) {
  const result = await call("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitForText(text, timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const found = await evaluate(`document.body && document.body.innerText.includes(${JSON.stringify(text)})`);
    if (found) return;
    await pause(200);
  }
  throw new Error(`timed out waiting for text: ${text}`);
}
async function setTextArea(label, value) {
  const focused = await evaluate(`(() => {
    const wrappers = [...document.querySelectorAll('[data-testid="stTextArea"]')];
    const wrapper = wrappers.find((node) => node.innerText.includes(${JSON.stringify(label)}));
    const input = wrapper && wrapper.querySelector('textarea');
    if (!input) return false;
    input.focus();
    input.select();
    return true;
  })()`);
  if (!focused) throw new Error(`textarea not found: ${label}`);
  await call("Input.insertText", {text: value});
  await call("Input.dispatchKeyEvent", {type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
  await call("Input.dispatchKeyEvent", {type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
  await pause(300);
}
async function clickButton(text, index = 0) {
  const clicked = await evaluate(`(() => {
    const normalize = (value) => value.replace(/\\s+/g, ' ').trim();
    const matches = [...document.querySelectorAll('button')].filter((button) => normalize(button.innerText) === ${JSON.stringify(text)} && !button.disabled);
    if (!matches[${index}]) return false;
    matches[${index}].click();
    return true;
  })()`);
  if (!clicked) throw new Error(`button not found or disabled: ${text} [${index}]`);
}
async function chooseRadio(text) {
  const clicked = await evaluate(`(() => {
    const labels = [...document.querySelectorAll('[data-testid="stRadio"] label')];
    const label = labels.find((node) => node.innerText.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(text)});
    if (!label) return false;
    label.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`radio not found: ${text}`);
  await pause(400);
}
async function viewport(width, height, mobile = false) {
  await call("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile});
  await pause(400);
}
async function screenshot(name) {
  const result = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
  const file = path.join(screenshotDir, name);
  fs.writeFileSync(file, Buffer.from(result.data, "base64"));
  return file.replaceAll("\\", "/");
}

await call("Page.enable");
await call("Runtime.enable");
await viewport(1440, 1200, false);
await waitForText("先说想去怎样的地方");
const evidence = [];
evidence.push({state: "intake_desktop", screenshot: await screenshot("01-intake-desktop.png")});

await setTextArea("自然语言旅行需求", "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。");
await clickButton("解析旅行需求");
await waitForText("需要补充");
await waitForText("查看全部识别字段与来源");
evidence.push({state: "clarification_desktop", screenshot: await screenshot("02-clarification-desktop.png")});

await setTextArea("补充回答或修改", "我们一共3人，2026年10月1日出发，舒适游。");
await clickButton("提交补充信息");
await waitForText("确认以后，再开始规划");
await waitForText("预计住宿晚数");
evidence.push({state: "confirmation_desktop", screenshot: await screenshot("03-confirmation-desktop.png")});

await chooseRadio("双方案比较");
await waitForText("确认并生成双方案比较");
await clickButton("确认并生成双方案比较");
await waitForText("比较事实，也解释取舍", 60000);
await waitForText("双方案对比");
evidence.push({state: "comparison_desktop", screenshot: await screenshot("04-comparison-desktop.png")});

await clickButton("查看每日行程", 0);
await waitForText("每日行程与预算");
await waitForText("业务状态代码：completed");
evidence.push({state: "detail_desktop", screenshot: await screenshot("05-detail-desktop.png")});

await viewport(390, 844, true);
evidence.push({state: "detail_narrow", screenshot: await screenshot("06-detail-narrow.png")});
await clickButton("返回双方案比较");
await waitForText("比较事实，也解释取舍");
evidence.push({state: "comparison_narrow", screenshot: await screenshot("07-comparison-narrow.png")});

const pageText = await evaluate("document.body.innerText");
const report = {
  demo: "stage_d3_real_edge_streamlit_acceptance",
  evidence,
  assertions: {
    actual_streamlit_dom: true,
    real_fastapi_flow: true,
    natural_multiturn_completed: true,
    dual_plan_comparison_rendered: pageText.includes("双方案对比"),
    mock_notice_visible: pageText.includes("示例 Mock 数据") && pageText.includes("不代表预订"),
    narrow_viewport_checked: true,
  },
  limitations: [
    "浏览器流程使用确定性 Mock Parser 与 Provider，不是实时旅行服务。",
    "自动化浏览器验收不是实际用户可用性研究。",
  ],
};
fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n", "utf8");
console.log(JSON.stringify(report, null, 2));
socket.close();
