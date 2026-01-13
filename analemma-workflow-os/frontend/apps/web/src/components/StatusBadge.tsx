import React from 'react';
import { Badge } from '@/components/ui/badge';
import { PlayCircle, PauseCircle, CheckCircle, Clock } from 'lucide-react';

interface Props {
  status?: string | null;
  className?: string;
  showLabel?: boolean;
}

const getStatusColor = (status?: string) => {
  switch (status) {
    case 'RUNNING': return 'text-green-600 bg-green-50 border-green-200';
    case 'PAUSED_FOR_HITP': return 'text-orange-600 bg-orange-50 border-orange-200';
    case 'COMPLETE': return 'text-blue-600 bg-blue-50 border-blue-200';
    default: return 'text-gray-600 bg-gray-50 border-gray-200';
  }
};

const getStatusIcon = (status?: string) => {
  switch (status) {
    case 'RUNNING': return <PlayCircle className="w-4 h-4" />;
    case 'PAUSED_FOR_HITP': return <PauseCircle className="w-4 h-4" />;
    case 'COMPLETE': return <CheckCircle className="w-4 h-4" />;
    default: return <Clock className="w-4 h-4" />;
  }
};

const StatusBadge: React.FC<Props> = ({ status, className = '', showLabel = true }) => {
  const color = getStatusColor(status);
  const icon = getStatusIcon(status);
  const label = status || 'UNKNOWN';

  return (
    <Badge className={`text-xs flex items-center gap-2 ${color} ${className}`}>
      {icon}
      {showLabel && <span>{label}</span>}
    </Badge>
  );
};

export default React.memo(StatusBadge);
