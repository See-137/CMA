import React from 'react';

interface LoadingSpinnerProps {
  text?: string;
  size?: 'sm' | 'md' | 'lg';
}

const sizeMap = {
  sm: 'h-5 w-5',
  md: 'h-8 w-8',
  lg: 'h-12 w-12',
};

const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ text, size = 'md' }) => {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      <div
        className={`${sizeMap[size]} animate-spin rounded-full border-2 border-slate-200 border-t-teal-600`}
      />
      {text && <p className="mt-3 text-sm text-slate-500">{text}</p>}
    </div>
  );
};

export default LoadingSpinner;
