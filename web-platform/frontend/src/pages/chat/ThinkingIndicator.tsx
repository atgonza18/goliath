interface ThinkingIndicatorProps {
  message?: string;
}

export function ThinkingIndicator({ message }: ThinkingIndicatorProps) {
  return (
    <div className="flex gap-3 animate-fade-in">
      <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-800 mt-0.5">
        <span className="text-[10px] font-semibold text-zinc-400">N</span>
      </div>
      <div className="flex-1 pt-1 flex items-center gap-1.5">
        {message ? (
          <span className="text-[12px] text-zinc-500">{message}</span>
        ) : (
          <div className="flex items-center gap-1">
            <div className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-dot-1" />
            <div className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-dot-2" />
            <div className="w-1.5 h-1.5 rounded-full bg-zinc-500 animate-dot-3" />
          </div>
        )}
      </div>
    </div>
  );
}
