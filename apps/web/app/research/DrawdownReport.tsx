import {drawdownReport,type DrawdownRow} from '../../lib/drawdown-report';

const quantstats='https://github.com/ranaroussi/quantstats/tree/9ef4c6d0a4ffe7831f6ebba6a5824fd33a90c05a';
const ffn='https://github.com/pmorissette/ffn/tree/383fc82319ee8d1a58300457dcf01ded2fd2049c';
const percentage=(value:number)=>`${(value*100).toFixed(4)}%`;
function duration(seconds:number){return `${(seconds/3600).toLocaleString('zh-CN',{maximumFractionDigits:2})} 小时`;}

export default function DrawdownReport({rows,totalReturn,runId}:{rows:DrawdownRow[];totalReturn:string;runId:string}){
 const report=drawdownReport(rows,totalReturn);
 function download(){
  const url=URL.createObjectURL(new Blob([JSON.stringify({run_id:runId,report,conventions:{ulcer:'sqrt(sum(drawdown^2)/N)',episode:'first underwater observation to recovery or last observation; unrecovered is censored',ratio:'signed compounded total return / maximum drawdown'},sources:{quantstats,ffn}},null,2)],{type:'application/json'}));
  const anchor=document.createElement('a');anchor.href=url;anchor.download=`fc666-drawdown-${runId}.json`;anchor.click();URL.revokeObjectURL(url);
 }
 return <section data-testid="drawdown-report"><h3>回撤诊断 · 恢复与持续时间</h3><p>参考 <a href={quantstats} target="_blank" rel="noreferrer">QuantStats（Apache-2.0）</a> 的回撤风险指标与 <a href={ffn} target="_blank" rel="noreferrer">ffn（MIT）</a> 的回撤分段思路，使用本次已保存的权益回撤计算。</p>
 {!report?<p role="status">回撤诊断不可用：至少需要两条按时间递增、带时区的有效回撤记录。</p>:<>
 <dl className="analysis-grid"><div><dt>回撤均方根（Ulcer）</dt><dd>{percentage(report.ulcerIndex)}</dd></div><div><dt>水下样本比例</dt><dd>{percentage(report.underwaterFraction)}</dd></div><div><dt>累计收益 / 最大回撤</dt><dd>{report.returnToDrawdown===null?'不可用（无回撤或超出数值范围）':report.returnToDrawdown.toFixed(4)}</dd></div><div><dt>最长已观察回撤区间</dt><dd>{duration(report.longestSeconds)}</dd></div></dl>
 <p>均方根按全部 N 个收盘样本计算，不年化；收益回撤比保留亏损负号。区间从首次水下收盘起算，到首次回到前高或最后样本为止。未恢复区间仅表示已观察时长；这些指标不预测未来恢复时间。</p>
 {report.episodes.length===0?<p>此窗口没有回撤区间。</p>:<div className="research-table"><table><thead><tr><th>首次水下 UTC</th><th>谷底 UTC</th><th>恢复 / 观察截止 UTC</th><th>最大深度</th><th>已观察时长</th><th>状态</th></tr></thead><tbody>{report.episodes.map(episode=><tr key={episode.start}><td>{episode.start}</td><td>{episode.trough}</td><td>{episode.end}</td><td>{percentage(episode.depth)}</td><td>{duration(episode.seconds)}</td><td>{episode.recovered?'已恢复':'尚未恢复'}</td></tr>)}</tbody></table></div>}
 <button onClick={download}>下载回撤诊断</button><small> 独立派生报告 v1，关联原 run_id；原始回测结果及复验哈希不变。</small></>}
 </section>;
}
