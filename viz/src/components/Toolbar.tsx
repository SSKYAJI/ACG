interface Props {
  isPlaying: boolean;
  speed: number;
  phaseLabel: string;
  progressPct: number;
  onPlay: () => void;
  onPause: () => void;
  onReset: () => void;
  onSpeedChange: (speed: number) => void;
}

const SPEEDS = [0.5, 1, 2, 4] as const;

export function Toolbar(props: Props) {
  return (
    <div className="toolbar">
      <span className="phase">{props.phaseLabel}</span>
      <div className="progress">
        <div
          className="progress-fill"
          style={{ width: `${Math.min(100, Math.max(0, props.progressPct))}%` }}
        />
      </div>
      <span className="spacer" />
      <span className="speed-label">speed</span>
      <div className="speed-group">
        {SPEEDS.map((s) => (
          <button
            key={s}
            className={`speed${s === props.speed ? " active" : ""}`}
            onClick={() => props.onSpeedChange(s)}
          >
            {s}×
          </button>
        ))}
      </div>
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
