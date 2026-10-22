import { useEffect, useMemo, useRef, useState } from 'react';
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query';
import { Link, Route, Router as WouterRouter, Switch, useLocation } from 'wouter';
import { Activity, ArrowDownToLine, BookOpen, Boxes, Check, CircleHelp, Clock3, Cpu, Gauge, GitCompareArrows, Network, Play, Radio, RotateCcw, Settings2, Square, Waypoints } from 'lucide-react';
import { Background, BackgroundVariant, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  getGetAlgorithmsQueryKey, getGetControllerStateQueryKey, getGetExperimentsQueryKey, getGetQueueMetricsQueryKey, getGetSimulationQueryKey, getGetTopologiesQueryKey,
  useGetAlgorithms, useGetExperiments, useGetQueueMetrics, useGetSimulation, useGetTopologies,
  useGetControllerState, useInstallFlowRule, useInstallQueueRate, useResetSimulation, useRunExperiment, useStartSimulation, useStopSimulation,
} from '@workspace/api-client-react';
import type { Algorithm, ExperimentInput, QueueMetricPoint, SimulationStartInput, SimulationState, Topology } from '@workspace/api-client-react';
import { useConsoleStore } from '@/lib/console-store';
import { useSimulationStream } from '@/hooks/use-simulation-stream';
import './index.css';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });
const navItems = [
  { href: '/', label: 'Simulation', icon: Activity },
  { href: '/experiments', label: 'Experiments', icon: GitCompareArrows },
  { href: '/algorithms', label: 'Algorithms', icon: Waypoints },
  { href: '/settings', label: 'Settings & setup', icon: Settings2 },
];

function Shell({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Network size={19} /></div><div className="brand-copy"><strong>NeuroRoute</strong><small>SDN LAB / 01</small></div></div>
      <div className="nav-label">Workspace</div>
      <nav className="nav-list" aria-label="Main navigation">{navItems.map(({ href, label, icon: Icon }) =>
        <Link href={href} key={href} className={`nav-link ${location === href ? 'active' : ''}`} data-testid={`link-nav-${label.toLowerCase().replaceAll(' ', '-')}`}><Icon size={16} /><span>{label}</span></Link>)}</nav>
      <div className="sidebar-bottom"><div className="connection"><i className="pulse-dot" /> Local simulator interface</div><div className="sidebar-note">EXPERIMENT CONTROL<br />RESEARCH BUILD · VALIDATION PENDING</div></div>
    </aside>
    <div className="main-area"><header className="topbar"><div className="crumb">NeuroRoute <span style={{ color: '#b3b9b0' }}>/</span> SDN research console</div><div className="top-right"><span className="env-pill">LOCAL WORKSPACE</span><span>CONTROL ROOM&nbsp; 01</span></div></header>{children}</div>
  </div>;
}

function Panel({ title, icon: Icon, meta, children, className = '' }: { title: string; icon: typeof Activity; meta?: string; children: React.ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-head"><div className="panel-title"><Icon size={15} />{title}</div>{meta && <span className="panel-kicker">{meta}</span>}</div>{children}</section>;
}
function LoadingBlock({ lines = 3 }: { lines?: number }) {
  return <div className="load-state" aria-label="Loading"><div style={{ display: 'grid', gap: 10, maxWidth: 280, margin: 'auto' }}>{Array.from({ length: lines }, (_, i) => <div className="skeleton" key={i} style={{ width: `${100 - i * 13}%` }} />)}</div></div>;
}
function ErrorBlock({ retry, message = 'The local API could not return this resource.' }: { retry: () => void; message?: string }) {
  return <div className="error-box" role="alert"><span>{message}</span><button className="btn" onClick={retry} data-testid="button-retry">Retry</button></div>;
}
function EmptyBlock({ title, copy }: { title: string; copy: string }) {
  return <div className="empty-state"><div className="empty-icon"><Boxes size={18} /></div><strong>{title}</strong><p>{copy}</p></div>;
}
function PageHead({ eyebrow, title, subtitle, right }: { eyebrow: string; title: string; subtitle: string; right?: React.ReactNode }) {
  return <div className="page-head"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p className="page-subtitle">{subtitle}</p></div>{right}</div>;
}
function formatDuration(seconds: number) {
  const mins = Math.floor((seconds || 0) / 60).toString().padStart(2, '0');
  return `${mins}:${Math.floor((seconds || 0) % 60).toString().padStart(2, '0')}`;
}
function statusLabel(status?: string) { return status || 'unknown'; }

function Metrics({ simulation }: { simulation?: SimulationState }) {
  const items = [
    { label: 'Mean latency', value: simulation ? simulation.averageLatencyMs.toFixed(2) : '—', unit: 'ms', foot: 'Current simulation state' },
    { label: 'Throughput', value: simulation ? simulation.throughputMbps.toFixed(2) : '—', unit: 'Mbps', foot: 'Reported by simulator' },
    { label: 'Active flows', value: simulation ? String(simulation.activeFlows) : '—', unit: '', foot: 'Concurrent flow count' },
    { label: 'Packet loss', value: simulation ? simulation.packetLossPercent.toFixed(3) : '—', unit: '%', foot: 'Reported by simulator' },
  ];
  return <div className="metrics-strip">{items.map((m) => <div className="metric-card" key={m.label} data-testid={`metric-${m.label.toLowerCase().replaceAll(' ', '-')}`}><div className="metric-label">{m.label}</div><div className="metric-value">{m.value}<span className="metric-unit">{m.unit}</span></div><div className="metric-foot">{m.foot}</div></div>)}</div>;
}

function TopologyView({ topology }: { topology?: Topology }) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 600, height: 280 });
  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      setCanvasSize(current => Math.round(current.width) === Math.round(width) && Math.round(current.height) === Math.round(height)
        ? current
        : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [topology?.id]);
  if (!topology) return <div className="empty-state"><div className="empty-icon"><Network size={18} /></div><strong>No topology selected</strong><p>Select a topology once the catalog has loaded.</p></div>;
  const nodes = topology.nodes || [];
  const minX = Math.min(...nodes.map(n => n.x)), maxX = Math.max(...nodes.map(n => n.x));
  const minY = Math.min(...nodes.map(n => n.y)), maxY = Math.max(...nodes.map(n => n.y));
  const xSpan = maxX - minX || 1, ySpan = maxY - minY || 1;
  const padX = Math.max(42, Math.min(72, canvasSize.width * 0.12));
  const padY = Math.max(42, Math.min(64, canvasSize.height * 0.17));
  const graphWidth = Math.max(1, canvasSize.width - padX * 2);
  const graphHeight = Math.max(1, canvasSize.height - padY * 2);
  const graphNodes: Node[] = nodes.map(node => ({
    id: node.id,
    position: {
      x: padX + (maxX === minX ? 0.5 : (node.x - minX) / xSpan) * graphWidth,
      y: padY + (maxY === minY ? 0.5 : (node.y - minY) / ySpan) * graphHeight,
    },
    data: { label: node.label },
    className: 'topology-flow-node',
    type: 'default',
  }));
  const links = topology.links || [];
  const maxUtilization = Math.max(0, ...links.map(link => link.utilization));
  const graphEdges: Edge[] = links.map(link => {
    const relativeLoad = maxUtilization > 0 ? link.utilization / maxUtilization : 0;
    const color = relativeLoad >= 0.75 ? '#c88d47' : relativeLoad >= 0.4 ? '#6f9c83' : '#a9b9ad';
    return {
      id: link.id,
      source: link.source,
      target: link.target,
      label: String(link.utilization),
      labelShowBg: true,
      labelBgStyle: { fill: '#f7f6ef', fillOpacity: 0.94 },
      labelStyle: { fill: '#63746b', fontSize: 9, fontFamily: 'DM Mono, monospace' },
      style: { stroke: color, strokeWidth: 1.3 + relativeLoad * 1.4 },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 12, height: 12 },
    };
  });
  return <div ref={canvasRef} className="topology-canvas" data-testid="topology-map">
    {nodes.length ? <ReactFlow
      key={`${topology.id}-${Math.round(canvasSize.width)}x${Math.round(canvasSize.height)}`}
      nodes={graphNodes}
      edges={graphEdges}
      fitView
      fitViewOptions={{ padding: 0.22, minZoom: 0.35, maxZoom: 1.6 }}
      minZoom={0.25}
      maxZoom={2}
      nodesConnectable={false}
      elementsSelectable
      aria-label={`${topology.name} interactive network topology`}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d4ddd3" />
      <Controls showInteractive={false} />
    </ReactFlow> : <EmptyBlock title="Topology has no nodes" copy="The selected topology does not provide node coordinates." />}
    <div className="canvas-meta">{topology.nodeCount} NODES&nbsp; / &nbsp;{topology.linkCount} LINKS</div>
  </div>;
}

function QueueChart({ points, loading, error, retry }: { points?: QueueMetricPoint[]; loading: boolean; error: boolean; retry: () => void }) {
  if (loading) return <LoadingBlock lines={4} />;
  if (error) return <ErrorBlock retry={retry} message="Queue metrics are unavailable." />;
  if (!points?.length) return <EmptyBlock title="No queue samples yet" copy="Queue occupancy will appear here when the simulator has metrics for the selected window." />;
  const byTime = new Map<number, { ls: number; ts: number; count: number }>();
  points.forEach(p => { const item = byTime.get(p.elapsedSeconds) || { ls: 0, ts: 0, count: 0 }; item.ls += p.lsOccupancy; item.ts += p.tsOccupancy; item.count++; byTime.set(p.elapsedSeconds, item); });
  const samples = Array.from(byTime.entries()).sort((a, b) => a[0] - b[0]).slice(-40);
  const vals = samples.flatMap(([, v]) => [v.ls / v.count, v.ts / v.count]);
  const max = Math.max(1, ...vals);
  const x = (i: number) => 38 + i * (500 / Math.max(1, samples.length - 1));
  const y = (v: number) => 150 - (v / max) * 122;
  const path = (key: 'ls' | 'ts') => samples.map(([, val], i) => `${i ? 'L' : 'M'} ${x(i)} ${y(val[key] / val.count)}`).join(' ');
  return <div className="chart-wrap" data-testid="queue-chart"><svg className="chart-svg" viewBox="0 0 560 175" preserveAspectRatio="none">
    {[28, 68, 108, 150].map(yv => <line key={yv} className="chart-grid" x1="38" x2="538" y1={yv} y2={yv} />)}
    <text className="chart-label" x="2" y="31">{max.toFixed(1)}</text><text className="chart-label" x="16" y="153">0</text>
    <path d={path('ls')} fill="none" stroke="#428d74" strokeWidth="2.2" strokeLinejoin="round" /><path d={path('ts')} fill="none" stroke="#d89d56" strokeWidth="2.2" strokeLinejoin="round" />
    {samples.length > 1 && <><text className="chart-label" x="38" y="170">{samples[0][0]}s</text><text className="chart-label" x="508" y="170">{samples[samples.length - 1][0]}s</text></>}
  </svg></div>;
}

function ControllerManagement() {
  const qc = useQueryClient();
  const controller = useGetControllerState();
  const installRate = useInstallQueueRate();
  const installRule = useInstallFlowRule();
  const switchId = useConsoleStore(state => state.selectedSwitchId);
  const setSwitchId = useConsoleStore(state => state.setSelectedSwitchId);
  const [lsShare, setLsShare] = useState(50);
  const [ruleId, setRuleId] = useState('');
  const [matchJson, setMatchJson] = useState('{\n  \n}');
  const [actionsJson, setActionsJson] = useState('{\n  \n}');
  const [formError, setFormError] = useState('');
  useEffect(() => {
    if (!switchId && controller.data?.switches?.length) setSwitchId(controller.data.switches[0].switchId);
  }, [controller.data, switchId]);
  const selectedSwitch = controller.data?.switches?.find(item => item.switchId === switchId);
  useEffect(() => {
    if (selectedSwitch) setLsShare(selectedSwitch.queues.lsRatePercent);
  }, [selectedSwitch?.switchId, selectedSwitch?.queues.lsRatePercent]);
  const refreshController = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: getGetControllerStateQueryKey() }),
      qc.invalidateQueries({ queryKey: getGetSimulationQueryKey() }),
    ]);
  };
  const submitRate = async () => {
    setFormError('');
    try {
      await installRate.mutateAsync({ data: { switchId, lsRatePercent: lsShare } });
      await refreshController();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Queue share could not be installed.');
    }
  };
  const submitRule = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    try {
      const match = JSON.parse(matchJson);
      const actions = JSON.parse(actionsJson);
      if (!match || Array.isArray(match) || typeof match !== 'object') throw new Error('Match must be a JSON object.');
      if (!actions || Array.isArray(actions) || typeof actions !== 'object') throw new Error('Actions must be a JSON object.');
      await installRule.mutateAsync({
        data: { switchId, ...(ruleId.trim() ? { id: ruleId.trim() } : {}), match, actions },
      });
      await refreshController();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Flow rule could not be installed.');
    }
  };
  return <Panel title="Controller management" icon={Cpu} meta={controller.data?.mode || 'CONTROLLER STATE'} className="controller-panel">
    <div className="panel-body">
      {controller.isLoading ? <LoadingBlock lines={5} /> : controller.isError ? <ErrorBlock retry={() => void controller.refetch()} message="Controller state could not be loaded." /> : !controller.data ? <EmptyBlock title="Controller state unavailable" copy="No controller state was returned by the local API." /> : !controller.data.switches?.length ? <EmptyBlock title="No switches registered" copy="The controller response contains no switch records to manage." /> : <>
        {formError && <div className="error-box" role="alert" style={{ marginBottom: 15 }}><span>{formError}</span><button className="btn" onClick={() => setFormError('')}>Dismiss</button></div>}
        <div className="controller-toolbar">
          <div className="field" style={{ maxWidth: 310 }}><label htmlFor="controller-switch">Selected switch</label><select id="controller-switch" value={switchId} onChange={e => setSwitchId(e.target.value)} data-testid="select-controller-switch">{controller.data.switches.map(item => <option key={item.switchId} value={item.switchId}>{item.switchId}</option>)}</select></div>
          <div className="controller-mode"><span className="panel-kicker">CONTROLLER MODE</span><strong>{controller.data.mode}</strong></div>
        </div>
        {selectedSwitch && <div className="controller-grid">
          <section className="controller-subpanel">
            <div className="subpanel-head"><div><strong>Queue allocation</strong><span>Per-switch queue state</span></div><Gauge size={15} /></div>
            <div className="queue-readout">
              <div><span>LS occupancy</span><strong>{selectedSwitch.queues.lsOccupancy}</strong></div>
              <div><span>TS occupancy</span><strong>{selectedSwitch.queues.tsOccupancy}</strong></div>
              <div><span>LS rate</span><strong>{selectedSwitch.queues.lsRatePercent}<small>%</small></strong></div>
              <div><span>TS rate</span><strong>{selectedSwitch.queues.tsRatePercent}<small>%</small></strong></div>
            </div>
            <div className="queue-install">
              <div className="field"><label htmlFor="ls-share">Install LS queue share · %</label><input id="ls-share" type="number" min="0" max="100" value={lsShare} onChange={e => setLsShare(Math.max(0, Math.min(100, Number(e.target.value))))} data-testid="input-controller-ls-share" /></div>
              <button className="btn btn-primary" onClick={() => void submitRate()} disabled={!switchId || installRate.isPending} data-testid="button-install-queue-rate"><Check size={13} />{installRate.isPending ? 'Installing…' : 'Apply LS share'}</button>
            </div>
          </section>
          <section className="controller-subpanel">
            <div className="subpanel-head"><div><strong>Port counters</strong><span>Controller-reported packet totals</span></div><Activity size={15} /></div>
            {controller.data.ports?.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Port</th><th>RX packets</th><th>TX packets</th></tr></thead><tbody>{controller.data.ports.map((port, i) => <tr key={`${port.port}-${i}`}><td className="mono">{port.port}</td><td className="mono">{port.rxPackets.toLocaleString()}</td><td className="mono">{port.txPackets.toLocaleString()}</td></tr>)}</tbody></table></div> : <div className="inline-empty">No port counters returned.</div>}
          </section>
          <section className="controller-subpanel flow-table">
            <div className="subpanel-head"><div><strong>Flow table</strong><span>{selectedSwitch.flowTable?.length || 0} rules · {selectedSwitch.switchId}</span></div><Waypoints size={15} /></div>
            {selectedSwitch.flowTable?.length ? <div className="table-scroll"><table className="data-table"><thead><tr><th>Match</th><th>Actions</th><th>Packets</th></tr></thead><tbody>{selectedSwitch.flowTable.map((rule, i) => <tr key={`${selectedSwitch.switchId}-${i}`}><td><pre className="json-cell">{JSON.stringify(rule.match, null, 2)}</pre></td><td><pre className="json-cell">{JSON.stringify(rule.actions, null, 2)}</pre></td><td className="mono">{rule.packets.toLocaleString()}</td></tr>)}</tbody></table></div> : <div className="inline-empty">This switch has no flow rules in the controller response.</div>}
          </section>
          <section className="controller-subpanel">
            <div className="subpanel-head"><div><strong>Install flow rule</strong><span>Submit a match/actions JSON object to this switch</span></div><Check size={15} /></div>
            <form className="rule-form" onSubmit={submitRule}>
              <div className="field"><label htmlFor="flow-rule-id">Rule ID · optional</label><input id="flow-rule-id" value={ruleId} onChange={e => setRuleId(e.target.value)} placeholder="Leave empty if not required" data-testid="input-flow-rule-id" /></div>
              <div className="field"><label htmlFor="flow-match">Match · JSON object</label><textarea id="flow-match" value={matchJson} onChange={e => setMatchJson(e.target.value)} spellCheck={false} data-testid="input-flow-match" /></div>
              <div className="field"><label htmlFor="flow-actions">Actions · JSON object</label><textarea id="flow-actions" value={actionsJson} onChange={e => setActionsJson(e.target.value)} spellCheck={false} data-testid="input-flow-actions" /></div>
              <button className="btn btn-primary" type="submit" disabled={!switchId || installRule.isPending} data-testid="button-install-flow-rule"><Check size={13} />{installRule.isPending ? 'Installing…' : 'Install rule'}</button>
            </form>
          </section>
        </div>}
      </>}
    </div>
  </Panel>;
}

function Dashboard() {
  const qc = useQueryClient();
  const streamStatus = useSimulationStream();
  const topologies = useGetTopologies();
  const algorithms = useGetAlgorithms();
  const sim = useGetSimulation();
  const metrics = useGetQueueMetrics({ windowSeconds: 120 });
  const start = useStartSimulation();
  const stop = useStopSimulation();
  const reset = useResetSimulation();
  const topologyId = useConsoleStore(state => state.selectedTopologyId);
  const setTopologyId = useConsoleStore(state => state.setSelectedTopologyId);
  const [algorithm, setAlgorithm] = useState('');
  const [duration, setDuration] = useState(120);
  const [seed, setSeed] = useState(41);
  const [lsRate, setLsRate] = useState(50);
  const [tsRate, setTsRate] = useState(50);
  const [actionError, setActionError] = useState('');
  useEffect(() => { if (!topologyId && topologies.data?.length) setTopologyId(topologies.data[0].id); }, [topologies.data, topologyId]);
  useEffect(() => { if (!algorithm && algorithms.data?.length) setAlgorithm(algorithms.data.find(a => a.available)?.id || algorithms.data[0].id); }, [algorithms.data, algorithm]);
  const topology = topologies.data?.find(t => t.id === topologyId);
  const running = sim.data?.status === 'running';
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: getGetSimulationQueryKey() }),
    qc.invalidateQueries({ queryKey: getGetQueueMetricsQueryKey({ windowSeconds: 120 }) }),
    qc.invalidateQueries({ queryKey: getGetExperimentsQueryKey() }),
  ]);
  const doAction = async (fn: () => Promise<unknown>) => { setActionError(''); try { await fn(); await refresh(); } catch (e) { setActionError(e instanceof Error ? e.message : 'Action could not be completed.'); } };
  const onStart = () => {
    if (!topologyId || !algorithm) return;
    const data: SimulationStartInput = { topologyId, algorithm, durationSeconds: duration, seed, lsRate, tsRate };
    void doAction(() => start.mutateAsync({ data }));
  };
  return <main className="page">
    <PageHead eyebrow="01 / Live environment" title="Simulation overview" subtitle="Configure a topology, choose a routing method, and inspect the simulator’s current state." right={<div className="dashboard-statuses"><div className="status-line"><i className={`status-marker ${streamStatus === 'live' ? 'running' : 'reconnecting'}`} />{streamStatus === 'live' ? 'STREAM LIVE' : streamStatus === 'connecting' ? 'CONNECTING' : 'RECONNECTING'}</div><div className="status-line"><i className={`status-marker ${running ? 'running' : ''}`} />{running ? 'RUN IN PROGRESS' : 'CONTROL READY'}</div></div>} />
    {actionError && <div className="error-box" style={{ marginBottom: 15 }} role="alert"><span>{actionError}</span><button className="btn" onClick={() => setActionError('')}>Dismiss</button></div>}
    {(topologies.isLoading || algorithms.isLoading || sim.isLoading) ? <LoadingBlock lines={5} /> : (
      <>
        {topologies.isError && <ErrorBlock retry={() => void topologies.refetch()} message="Topology catalog could not be loaded." />}
        {algorithms.isError && <ErrorBlock retry={() => void algorithms.refetch()} message="Routing catalog could not be loaded." />}
        {sim.isError && <ErrorBlock retry={() => void sim.refetch()} message="Current simulation state could not be loaded." />}
        <Metrics simulation={sim.data} />
        <div className="dashboard-grid">
          <Panel title="Network topology" icon={Network} meta={topology?.name || 'CATALOG'} className="topology-panel">
            <div className="panel-body" style={{ paddingTop: 13 }}>
              {topologies.data?.length ? <><div className="field" style={{ maxWidth: 280, marginBottom: 12 }}><label htmlFor="topology-select">Active topology</label><select id="topology-select" value={topologyId} onChange={e => setTopologyId(e.target.value)} data-testid="select-topology">{topologies.data.map(t => <option value={t.id} key={t.id}>{t.name}</option>)}</select></div>
                <TopologyView topology={topology} /><p style={{ fontSize: 10, lineHeight: 1.6, color: '#77847d', margin: '10px 0 0' }}>{topology?.description}</p></>
                : <EmptyBlock title="Topology catalog is empty" copy="No network topology is currently available from the local API." />}
            </div>
          </Panel>
          <Panel title="Run controls" icon={Radio} meta="SIMULATION"><div className="panel-body">
            <div className="run-status"><div className="status-name"><i className={`status-marker ${running ? 'running' : ''}`} />{statusLabel(sim.data?.status)}</div><div className="elapsed">{formatDuration(sim.data?.elapsedSeconds || 0)} / {formatDuration(sim.data?.durationSeconds || duration)}</div></div>
            <div className="control-grid">
              <div className="field"><label htmlFor="algorithm-select">Routing algorithm</label><select id="algorithm-select" value={algorithm} onChange={e => setAlgorithm(e.target.value)} disabled={!algorithms.data?.length} data-testid="select-algorithm">{(algorithms.data || []).map(a => <option value={a.id} key={a.id} disabled={!a.available}>{a.name}{a.available ? '' : ' · unavailable'}</option>)}</select></div>
              <div className="field"><label htmlFor="run-duration">Duration · seconds</label><input id="run-duration" type="number" min="10" max="3600" value={duration} onChange={e => setDuration(Math.max(10, Math.min(3600, Number(e.target.value))))} data-testid="input-duration" /></div>
              <div className="field"><label htmlFor="run-seed">Random seed</label><input id="run-seed" type="number" value={seed} onChange={e => setSeed(Number(e.target.value))} data-testid="input-seed" /></div>
              <div className="field"><label htmlFor="ls-rate">LS traffic rate · %</label><input id="ls-rate" type="number" min="0" max="100" value={lsRate} onChange={e => setLsRate(Math.max(0, Math.min(100, Number(e.target.value))))} data-testid="input-ls-rate" /></div>
              <div className="field"><label htmlFor="ts-rate">TS traffic rate · %</label><input id="ts-rate" type="number" min="0" max="100" value={tsRate} onChange={e => setTsRate(Math.max(0, Math.min(100, Number(e.target.value))))} data-testid="input-ts-rate" /></div>
            </div>
            <div className="actions">
              <button className="btn btn-primary" onClick={onStart} disabled={!topologyId || !algorithm || !algorithms.data?.find(a => a.id === algorithm)?.available || running || start.isPending} data-testid="button-start"><Play size={13} />{start.isPending ? 'Starting…' : 'Start simulation'}</button>
              <button className="btn" onClick={() => void doAction(() => stop.mutateAsync())} disabled={!running || stop.isPending} data-testid="button-stop"><Square size={12} />Stop</button>
              <button className="btn btn-quiet" onClick={() => void doAction(() => reset.mutateAsync())} disabled={reset.isPending} data-testid="button-reset"><RotateCcw size={12} />Reset</button>
            </div>
          </div></Panel>
          <Panel title="Queue occupancy" icon={Gauge} meta="LAST 120 SECONDS">
            <div className="panel-body"><div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 7 }}><span className="panel-kicker">MEAN BY ELAPSED SECOND</span><div className="legend"><span><i />LS</span><span><i className="ts" />TS</span></div></div>
              <QueueChart points={metrics.data} loading={metrics.isLoading} error={metrics.isError} retry={() => void metrics.refetch()} />
            </div>
          </Panel>
          <Panel title="Run context" icon={Cpu} meta={sim.data?.id ? `ID ${sim.data.id.slice(0, 10)}` : 'NO RUN ID'}>
            <div className="panel-body">
              <div className="info-row"><span className="info-label">Algorithm</span><span className="info-copy">{algorithms.data?.find(a => a.id === sim.data?.algorithm)?.name || sim.data?.algorithm || '—'}</span></div>
              <div className="info-row"><span className="info-label">Topology</span><span className="info-copy">{topologies.data?.find(t => t.id === sim.data?.topologyId)?.name || '—'}</span></div>
              <div className="info-row"><span className="info-label">Last updated</span><span className="info-copy">{sim.data?.updatedAt ? new Date(sim.data.updatedAt).toLocaleString() : '—'}</span></div>
              <div className="info-row"><span className="info-label">State source</span><span className="info-copy">Local simulator API</span></div>
            </div>
          </Panel>
          <ControllerManagement />
        </div>
      </>
    )}
  </main>;
}

function ExperimentsPage() {
  const qc = useQueryClient();
  const experiments = useGetExperiments();
  const topologies = useGetTopologies();
  const algorithms = useGetAlgorithms();
  const run = useRunExperiment();
  const [topologyId, setTopologyId] = useState('');
  const [duration, setDuration] = useState(120);
  const [seed, setSeed] = useState(41);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { if (!topologyId && topologies.data?.length) setTopologyId(topologies.data[0].id); }, [topologies.data, topologyId]);
  useEffect(() => { if (!chosen.length && algorithms.data?.length) setChosen(algorithms.data.filter(a => a.available).slice(0, 2).map(a => a.id)); }, [algorithms.data, chosen.length]);
  const toggle = (id: string) => setChosen(prev => prev.includes(id) ? prev.filter(item => item !== id) : prev.length < 8 ? [...prev, id] : prev);
  const submit = async () => {
    setError('');
    try {
      const input: ExperimentInput = { topologyId, durationSeconds: duration, algorithms: chosen, seed };
      await run.mutateAsync({ data: input });
      await qc.invalidateQueries({ queryKey: getGetExperimentsQueryKey() });
    } catch (e) { setError(e instanceof Error ? e.message : 'Experiment request failed.'); }
  };
  const refresh = () => { void experiments.refetch(); void topologies.refetch(); void algorithms.refetch(); };
  return <main className="page">
    <PageHead eyebrow="02 / Comparative evaluation" title="Experiments" subtitle="Queue a repeatable multi-algorithm run and review the results returned by the simulator." />
    {error && <div className="error-box" style={{ marginBottom: 15 }} role="alert"><span>{error}</span><button className="btn" onClick={() => setError('')}>Dismiss</button></div>}
    <div className="experiment-layout">
      <Panel title="New comparison" icon={GitCompareArrows} meta="REPEATABLE RUN"><div className="panel-body">
        {(topologies.isLoading || algorithms.isLoading) ? <LoadingBlock lines={4} /> : topologies.isError || algorithms.isError ? <ErrorBlock retry={refresh} message="Experiment setup catalogs could not be loaded." /> : !topologies.data?.length || !algorithms.data?.some(a => a.available) ? <EmptyBlock title="Setup unavailable" copy="A topology and at least one available algorithm are required." /> : <>
          <div className="field"><label htmlFor="exp-topology">Topology</label><select id="exp-topology" value={topologyId} onChange={e => setTopologyId(e.target.value)}>{topologies.data.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></div>
          <div className="control-grid" style={{ marginTop: 12 }}>
            <div className="field"><label htmlFor="exp-duration">Duration · seconds</label><input id="exp-duration" type="number" min="10" max="3600" value={duration} onChange={e => setDuration(Math.max(10, Math.min(3600, Number(e.target.value))))} /></div>
            <div className="field"><label htmlFor="exp-seed">Random seed</label><input id="exp-seed" type="number" value={seed} onChange={e => setSeed(Number(e.target.value))} /></div>
          </div>
          <div className="field" style={{ marginTop: 15 }}><label>Algorithms · {chosen.length} selected, up to 8</label><div className="check-list">{algorithms.data?.map(algo => <label className="check-row" key={algo.id}><input type="checkbox" checked={chosen.includes(algo.id)} disabled={!algo.available || (!chosen.includes(algo.id) && chosen.length >= 8)} onChange={() => toggle(algo.id)} />{algo.name}<span style={{ marginLeft: 'auto', color: '#859189', fontFamily: 'var(--app-font-mono)', fontSize: 9 }}>{algo.available ? algo.family : 'UNAVAILABLE'}</span></label>)}</div></div>
          <div className="actions"><button className="btn btn-primary" onClick={() => void submit()} disabled={!topologyId || !chosen.length || run.isPending} data-testid="button-run-experiment"><Play size={13} />{run.isPending ? 'Submitting…' : 'Run comparison'}</button></div>
        </>}
      </div></Panel>
      <Panel title="Saved runs" icon={Clock3} meta={`${experiments.data?.length || 0} RECORDED`}><div className="panel-body" style={{ padding: 0 }}>
        {experiments.isLoading ? <LoadingBlock lines={5} /> : experiments.isError ? <div style={{ padding: 18 }}><ErrorBlock retry={() => void experiments.refetch()} message="Saved runs could not be loaded." /></div> : !experiments.data?.length ? <EmptyBlock title="No experiment runs yet" copy="Comparative runs returned by the simulator will be collected here." /> :
          <div className="table-scroll"><table className="data-table" data-testid="table-experiments"><thead><tr><th>Topology / created</th><th>Algorithms</th><th>Duration</th><th>Status</th><th>Results</th></tr></thead><tbody>{experiments.data.map(exp => <tr key={exp.id} data-testid={`row-experiment-${exp.id}`}><td><strong>{exp.topologyName}</strong><div className="mono" style={{ color: '#8a958e', marginTop: 5 }}>{new Date(exp.createdAt).toLocaleString()}</div></td><td>{exp.algorithms.length} algorithms</td><td className="mono">{formatDuration(exp.durationSeconds)}</td><td><span className={`badge ${exp.status}`}>{exp.status}</span></td><td>{exp.results?.length || 0} result{exp.results?.length === 1 ? '' : 's'}</td></tr>)}</tbody></table>
            {experiments.data.map(exp => exp.results?.length ? <div key={`results-${exp.id}`} style={{ padding: '12px 18px 16px', borderTop: '1px solid #efede7' }}><div className="panel-kicker" style={{ marginBottom: 10 }}>RESULTS · {exp.topologyName} / {exp.id.slice(0, 10)}</div><div className="table-scroll"><table className="data-table"><thead><tr><th>Algorithm</th><th>Mean latency</th><th>Throughput</th><th>Packet loss</th><th>Mean queue</th></tr></thead><tbody>{exp.results.map((result, i) => <tr key={`${exp.id}-${result.algorithm}-${i}`}><td>{algorithms.data?.find(a => a.id === result.algorithm)?.name || result.algorithm}</td><td className="mono">{result.meanLatencyMs.toFixed(2)} ms</td><td className="mono">{result.throughputMbps.toFixed(2)} Mbps</td><td className="mono">{result.packetLossPercent.toFixed(3)}%</td><td className="mono">{result.meanQueueOccupancy.toFixed(2)}</td></tr>)}</tbody></table></div></div> : null)}
          </div>}
      </div></Panel>
    </div>
  </main>;
}

function AlgorithmsPage() {
  const algorithms = useGetAlgorithms();
  const topologies = useGetTopologies();
  const grouped = useMemo(() => {
    const groups = new Map<string, Algorithm[]>();
    (algorithms.data || []).forEach(a => groups.set(a.family || 'Other', [...(groups.get(a.family || 'Other') || []), a]));
    return [...groups.entries()];
  }, [algorithms.data]);
  return <main className="page"><PageHead eyebrow="03 / Method catalog" title="Routing algorithms" subtitle="The methods and baselines exposed by the current simulator API. Availability reflects the API response." right={<div className="status-line"><Waypoints size={14} />{algorithms.data?.length || 0} METHODS</div>} />
    {algorithms.isLoading || topologies.isLoading ? <LoadingBlock lines={6} /> : algorithms.isError ? <ErrorBlock retry={() => void algorithms.refetch()} message="Algorithm catalog could not be loaded." /> : !algorithms.data?.length ? <EmptyBlock title="No algorithms registered" copy="The local API returned an empty routing catalog." /> :
      <div style={{ display: 'grid', gap: 18 }}>{grouped.map(([family, entries]) => <section key={family}><div className="eyebrow" style={{ marginBottom: 9 }}>{family} <span style={{ color: '#9aa39c' }}> / {entries.length} METHOD{entries.length === 1 ? '' : 'S'}</span></div><div className="algo-list">{entries.map(algo => <article className="algo-card" key={algo.id} data-testid={`card-algorithm-${algo.id}`}><div><div className="algo-name">{algo.name}</div><div className="algo-family">{algo.family} · <span style={{ color: algo.available ? '#43846d' : '#a46c48' }}>{algo.available ? 'AVAILABLE' : 'UNAVAILABLE'}</span></div><p className="algo-desc">{algo.description}</p><div className="mono" style={{ color: '#9aa39c', marginTop: 9 }}>{algo.id}</div></div><div className="algo-symbol">{algo.available ? <Check size={17} /> : <CircleHelp size={17} />}</div></article>)}</div></section>)}
      <Panel title="Catalog scope" icon={BookOpen} meta="API-DEFINED"><div className="panel-body"><div className="info-row"><span className="info-label">Methods</span><span className="info-copy">{algorithms.data.length} returned from local API</span></div><div className="info-row"><span className="info-label">Topologies</span><span className="info-copy">{topologies.data?.length ?? '—'} available for evaluation</span></div><div className="info-row"><span className="info-label">Method claims</span><span className="info-copy">No paper-specific guarantees asserted. Validate implementations against your research protocol.</span></div></div></Panel>
      </div>}
  </main>;
}

function SettingsPage() {
  return <main className="page"><PageHead eyebrow="04 / Workspace notes" title="Settings & setup" subtitle="Environment context and experiment constraints for this frontend. API configuration remains managed by the host workspace." />
    <div className="settings-grid">
      <Panel title="Local backend" icon={Radio} meta="HOST MANAGED"><div className="panel-body">
        <div className="info-row"><span className="info-label">Connection</span><span className="info-copy">Requests use the generated NeuroRoute API client.</span></div>
        <div className="info-row"><span className="info-label">Base URL</span><span className="info-copy">Configured by the workspace client. No browser override is exposed here.</span></div>
        <div className="info-row"><span className="info-label">Resources</span><span className="info-copy"><span className="code-inline">/api/topologies</span> · <span className="code-inline">/api/algorithms</span> · <span className="code-inline">/api/simulation</span></span></div>
        <div className="info-row"><span className="info-label">Experiments</span><span className="info-copy"><span className="code-inline">/api/experiments</span> · queued runs and results from the backend</span></div>
      </div></Panel>
      <Panel title="Run constraints" icon={Settings2} meta="CLIENT VALIDATION"><div className="panel-body">
        <div className="info-row"><span className="info-label">Duration</span><span className="info-copy">10–3,600 seconds per run</span></div>
        <div className="info-row"><span className="info-label">Traffic rates</span><span className="info-copy">LS and TS inputs each accept 0–100%</span></div>
        <div className="info-row"><span className="info-label">Comparative batch</span><span className="info-copy">1–8 algorithms per experiment</span></div>
        <div className="info-row"><span className="info-label">Reproducibility</span><span className="info-copy">Set a seed before submission; preserve topology, algorithm list and duration with recorded runs.</span></div>
      </div></Panel>
      <Panel title="Validation status" icon={CircleHelp} meta="RESEARCH BUILD"><div className="panel-body">
        <div className="run-status"><div className="status-name"><i className="status-marker" />Paper validation not supplied</div><span className="badge idle">UNVERIFIED</span></div>
        <p style={{ fontSize: 11, lineHeight: 1.75, color: '#738079', margin: 0 }}>The referenced paper equation, algorithm procedure, and dashboard page list were not provided. This console reports simulator API values as returned and makes no paper-accurate mathematical or performance claim. Confirm model behavior and baselines against the source research before interpreting results.</p>
      </div></Panel>
      <Panel title="Data handling" icon={ArrowDownToLine} meta="API SOURCE"><div className="panel-body">
        <div className="info-row"><span className="info-label">Displayed values</span><span className="info-copy">Live topology, simulation, queue and experiment responses</span></div>
        <div className="info-row"><span className="info-label">Persistence</span><span className="info-copy">Saved runs are controlled by the backend API.</span></div>
        <div className="info-row"><span className="info-label">Export</span><span className="info-copy">No export endpoint is defined in the current client.</span></div>
        <div className="info-row"><span className="info-label">Help</span><span className="info-copy">Ask your workspace administrator for local backend startup and endpoint configuration.</span></div>
      </div></Panel>
    </div>
  </main>;
}
function NotFound() { return <main className="page"><PageHead eyebrow="ERROR / 404" title="Route not found" subtitle="That console destination does not exist." /><Link href="/" className="btn btn-primary">Return to simulation</Link></main>; }
function RoutedContent() {
  return <Shell><Switch><Route path="/" component={Dashboard} /><Route path="/experiments" component={ExperimentsPage} /><Route path="/algorithms" component={AlgorithmsPage} /><Route path="/settings" component={SettingsPage} /><Route component={NotFound} /></Switch></Shell>;
}
function App() {
  return <QueryClientProvider client={queryClient}><WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}><RoutedContent /></WouterRouter></QueryClientProvider>;
}

export default App;
