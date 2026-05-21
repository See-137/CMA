interface LoadingSpinnerProps {
  text?: string;
  size?: 'sm' | 'md' | 'lg';
}

const sizeMap = {
  sm: 'h-5 w-5',
  md: 'h-8 w-8',
  lg: 'h-12 w-12',
};

export default function LoadingSpinner({ text, size = 'md' }: LoadingSpinnerProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      <div
        className={`${sizeMap[size]} animate-spin rounded-full border-2 border-[color:var(--color-border)] border-t-gold-400`}
      />
      {text && <p className="mt-3 text-sm text-muted">{text}</p>}
    </div>
  );
}
