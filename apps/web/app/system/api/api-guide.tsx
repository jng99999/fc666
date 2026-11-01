'use client';
import Link from 'next/link';
import {useState} from 'react';
import styles from './page.module.css';
type Frontend={hosted:boolean;apiConfigured:boolean;directionalConfigured:boolean;goldConfigured:boolean};
const news=`{ "source": "与配置来源一致", "observed_at": "带时区ISO时间", "coverage_start": "覆盖起点", "coverage_end": "覆盖终点", "events": [{ "event_id": "稳定ID", "instruments": ["XAU/USD"], "impact": "HIGH", "scheduled_at": "事件时间", "known_at": "获知时间" }] }`;
const gold=`{ "instrument": "XAU/USD", "product_type": "SPOT", "currency": "USD", "candle_source": "与配置来源一致", "bars": [{ "open_time": 1793570400000, "open": "4000", "high": "4001", "low": "3999", "close": "4000.5", "volume": null }] }`;
export default function ApiGuide({frontend,ready}:{frontend:Frontend;ready:boolean}){
 const [copied,setCopied]=useState(''),[error,setError]=useState('');
 async function copy(label:string,value:string){setError('');try{await navigator.clipboard.writeText(value);setCopied(label);}catch{setError('浏览器未允许复制，请长按模板手动复制。');}}
 return <details className={`${styles.card} ${styles.guide}`}><summary>接口契约、验证步骤与运维说明</summary>
  <p>网页登录和密钥保存由服务器校验。网页只显示“已保存”，不返回密钥；不要在聊天、URL 查询参数或 Git 中放密钥。保存后需重新检查实际数据，再由管理员明确启用 AI 模拟。</p>
  <details className={styles.details}><summary>新闻接口契约</summary><p>HTTPS GET 携带 instrument 参数，可选 Bearer 认证；返回供应商真实时间和已核实覆盖，故障不能伪装为空日历。</p><pre className={styles.code}>{news}</pre><button type="button" className={styles.secondary} disabled={!ready} onClick={()=>void copy('新闻',news)}>复制新闻契约</button></details>
  <details className={styles.details}><summary>黄金历史契约</summary><p>HTTPS GET 参数 instrument=XAU/USD、interval=1m、limit=200、end_time=当前毫秒；至少 100 根、最多 200 根连续已闭合 1 分钟柱。示例仅表示结构，不是真实行情；未知成交量保留 null。</p><pre className={styles.code}>{gold}</pre><button type="button" className={styles.secondary} disabled={!ready} onClick={()=>void copy('黄金',gold)}>复制黄金契约</button></details>
  {copied?<p role="status">已复制{copied}接口契约，不含密钥。</p>:null}{error?<p role="alert" className={styles.error}>{error}</p>:null}
  <details className={styles.details}><summary>Vercel 连接与服务器运维</summary><p>网页保存使用服务器加密配置并热生效，不需要普通用户 SSH 编辑环境文件。原 research-worker.env / directional-ai.env 仅供运营者核对旧部署。Vercel 只管理站点到后端的地址，不填写模型密钥。</p><dl className={styles.configList}><div><dt>主服务地址</dt><dd>{frontend.apiConfigured?'已设置':'未设置'}</dd></div><div><dt>双向服务地址</dt><dd>{frontend.directionalConfigured?'已设置':'未设置'}</dd></div><div><dt>黄金服务地址</dt><dd>{frontend.goldConfigured?'已设置或沿用主服务':'未设置'}</dd></div></dl><p>{frontend.hosted?'当前为 Vercel 部署':'当前非 Vercel 部署'}；存在地址不代表请求成功。</p><a href="https://vercel.com/jng56/fc666-test/settings/environment-variables" className={styles.secondary} target="_blank" rel="noreferrer">打开 Vercel 环境配置 ↗</a></details>
  <ol className={styles.steps}><li>首次设置管理员后登录，填写服务商、真实模型名与密钥。</li><li>接入新闻和黄金现货历史，创建并授权独立 AI 研究账户。</li><li>保存后去 AI 研究页确认行情与新闻门禁，运行研究并核对建议和费用。</li><li>明确启用自动模拟，等待后续独立报价；FILLED 才是模拟成交，ACCEPTED 只是已接收。</li></ol><Link href="/research/ai" className={styles.inlineLink}>进入 AI 研究与自动模拟 →</Link><p>真实模型、新闻覆盖与策略效果须单独验证；不接入交易所私有下单密钥，实盘继续关闭。</p>
 </details>;
}
