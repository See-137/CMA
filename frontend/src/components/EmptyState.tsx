import { Inbox } from 'lucide-react';

interface EmptyStateProps {
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export default function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-16">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-surface-2 ring-1 ring-inset ring-[color:var(--color-border)]">
        <Inbox className="h-7 w-7 text-muted" />
      </div>
      <h3 className="font-display text-base font-semibold text-primary">{title}</h3>
      <p className="mt-1 max-w-sm text-center text-sm text-muted">{description}</p>
      {action && (
        <button onClick={action.onClick} className="btn-primary mt-5">
          {action.label}
        </button>
      )}
    </div>
  );
}
