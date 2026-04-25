interface Props {
  generatedAt: string;
  model: string;
  numTasks: number;
  numGroups: number;
  numConflicts: number;
  isPlaying: boolean;
  onPlay: () => void;
  onPause: () => void;
  onReset: () => void;
}

export function Toolbar(props: Props) {
  const generated = new Date(props.generatedAt);
  const generatedLabel = isNaN(generated.getTime())
    ? props.generatedAt
    : generated.toLocaleString();
  return (
    <div className="toolbar">
      <h1>ACG · agent_lock.json</h1>
      <span className="meta">
        {props.numTasks} tasks · {props.numGroups} groups ·{" "}
        {props.numConflicts} conflicts
      </span>
      <span className="meta">model: {props.model}</span>
      <span className="meta">{generatedLabel}</span>
      <span className="spacer" />
      {!props.isPlaying ? (
        <button className="primary" onClick={props.onPlay}>
          ▶ Play execution
        </button>
      ) : (
        <button onClick={props.onPause}>⏸ Pause</button>
      )}
      <button onClick={props.onReset}>Reset</button>
    </div>
  );
}
