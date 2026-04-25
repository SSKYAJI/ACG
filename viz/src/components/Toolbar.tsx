interface Props {
  isPlaying: boolean;
  onPlay: () => void;
  onPause: () => void;
  onReset: () => void;
}

export function Toolbar(props: Props) {
  return (
    <div className="toolbar">
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
