import { useState, useRef, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight, LayoutDashboard, RefreshCw, VideoOff, Loader2, Maximize, Minimize } from 'lucide-react';
import { Link } from 'react-router-dom';
import useMjpegStream from '@/hooks/useMjpegStream';
import useDashboardSocket from '@/hooks/useDashboardSocket';
import { CAMERAS } from '@/features/config/cameras';

const LABELS = {
  camera_01: 'Winter Section 1',
  camera_02: 'Winter Section 2',
  camera_03: 'Winter Section 3',
  camera_04: 'The Cashier',
  camera_05: 'Hookah Factory',
  camera_06: 'Bathhouse Ent',
  camera_07: 'Buffet',
  camera_08: 'The Kitchen 1',
  camera_09: 'The Kitchen 2',
  camera_10: 'The Kitchen 3',
  camera_11: 'The Ent 1',
  camera_12: 'The Ent 2',
  camera_13: 'The Ent 3',
  camera_14: 'Summer Section 1',
  camera_15: 'Summer Section 2',
  camera_16: 'River Section',
};

const cameras = CAMERAS.map((cam) => ({
  id: cam.id,
  label: LABELS[cam.id] || cam.name,
}));

function CameraTile({ cam, isMain, reloadSignal, onSelect }) {
  const { src, status, mode, retry, reloadToken, handleLoad, handleError } = useMjpegStream(cam.id, {
    probe: isMain,
    reloadSignal,
  });
  const containerRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      containerRef.current?.requestFullscreen?.();
    }
  };

  useEffect(() => {
    const onFsChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  return (
    <div
      ref={containerRef}
      onClick={onSelect}
      className={`relative flex flex-col items-center justify-center rounded-lg overflow-hidden transition-all ${
        isMain
          ? 'w-full aspect-video max-h-[58vh] bg-[#0f0f0f] border-2 border-[#4a90d9] shadow-lg'
          : 'w-[130px] flex-shrink-0 aspect-video bg-[#1a1a1a] border border-[#333] cursor-pointer hover:border-[#555]'
      }`}
    >
      {src && status !== 'error' && status !== 'noToken' && (
        <img
          key={reloadToken}
          src={src}
          alt={cam.label}
          className="absolute inset-0 w-full h-full object-cover"
          onLoad={handleLoad}
          onError={handleError}
        />
      )}

      {status === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center bg-[#1a1a1a]">
          <Loader2 className="animate-spin text-[#666]" size={24} />
        </div>
      )}

      {(status === 'error' || status === 'noToken') && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[#1a1a1a] text-center px-2">
          <VideoOff size={20} className="text-[#555]" />
          <span className="text-[#777] text-xs font-sans">{isMain ? 'No feed' : ''}</span>
          {isMain && (
            <button
              onClick={(e) => { e.stopPropagation(); retry(); }}
              className="flex items-center gap-1 px-2 py-1 rounded bg-[#222] border border-[#444] text-[#aaa] text-[11px] hover:text-white hover:bg-[#333]"
            >
              <RefreshCw size={12} />
              Retry
            </button>
          )}
        </div>
      )}

      {status === 'streaming' && mode === 'annotated' && (
        <span className="absolute top-1.5 left-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/60 text-[10px] font-sans text-green-400">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          AI DETECTION
        </span>
      )}

      {status === 'streaming' && mode === 'raw' && (
        <span className="absolute top-1.5 left-1.5 flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/60 text-[10px] font-sans text-gray-400">
          <span className="w-1.5 h-1.5 rounded-full bg-gray-400" />
          RAW
        </span>
      )}

      {isMain && (
        <button
          onClick={(e) => { e.stopPropagation(); toggleFullscreen(); }}
          className="absolute top-1.5 right-1.5 z-10 flex items-center justify-center p-1.5 rounded bg-black/60 text-white hover:bg-black/80 transition-colors"
          aria-label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        >
          {isFullscreen ? <Minimize size={14} /> : <Maximize size={14} />}
        </button>
      )}

      <span
        className={`absolute bottom-0 inset-x-0 px-1.5 py-0.5 text-[11px] font-sans text-center truncate z-10 ${
          isMain ? 'bg-black/60 text-white' : 'bg-[#222] text-[#888]'
        }`}
      >
        {cam.label}
      </span>
    </div>
  );
}

export default function Live() {
  const [mainIds, setMainIds] = useState([cameras[0]?.id, cameras[1]?.id].filter(Boolean));
  const [signals, setSignals] = useState({});
  const scrollContainerRef = useRef(null);

  const bumpSignal = useCallback((camId) => {
    setSignals((s) => ({ ...s, [camId]: (s[camId] || 0) + 1 }));
  }, []);

  useDashboardSocket({
    onEvent: useCallback((msg) => {
      const type = msg?.event_type;
      if ((type === 'CAMERA_STREAM_STARTED' || type === 'CAMERA_STREAM_STOPPED') && msg.camera_id) {
        bumpSignal(msg.camera_id);
      }
    }, [bumpSignal]),
  });

  const selectMain = (camId) => {
    if (mainIds.includes(camId)) return;
    setMainIds([mainIds[1], camId]);
  };

  const scroll = (direction) => {
    if (scrollContainerRef.current) {
      const scrollAmount = 300;
      scrollContainerRef.current.scrollBy({
        left: direction === 'left' ? -scrollAmount : scrollAmount,
        behavior: 'smooth',
      });
    }
  };

  return (
    <div className="flex flex-col gap-4 min-h-screen p-4 bg-[#111] box-border overflow-y-auto relative">

      <div className="w-full max-w-8xl mx-auto flex justify-between items-center z-10">
        <Link
          to="/dashboard"
          className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-[#1a1a1a] border border-[#333] text-sm text-[#aaa] hover:text-white hover:bg-[#222] hover:border-[#444] transition-all font-sans font-medium shadow-md"
        >
          <LayoutDashboard size={16} />
          <span>Dashboard</span>
        </Link>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full max-w-8xl mx-auto items-center my-auto flex-1">
        {mainIds.map((id) => {
          const cam = cameras.find((c) => c.id === id);
          if (!cam) return null;
          return <CameraTile key={id} cam={cam} isMain reloadSignal={signals[id] || 0} onSelect={() => {}} />;
        })}
      </div>

      <div className="relative flex items-center w-full max-w-7xl mx-auto px-8 group">

        <button
          onClick={() => scroll('left')}
          className="absolute left-0 p-1.5 rounded-full bg-[#222] border border-[#333] text-[#aaa] hover:text-white hover:bg-[#333] transition-colors z-10"
          aria-label="Scroll Left"
        >
          <ChevronLeft size={20} />
        </button>

        <div
          ref={scrollContainerRef}
          className="flex gap-2 overflow-x-hidden pb-1 w-full scroll-smooth"
        >
          {cameras.map((cam) => {
            const isMain = mainIds.includes(cam.id);
            return (
              <div key={cam.id} className={`${isMain ? 'opacity-60 scale-95' : ''} transition-all`}>
                <CameraTile cam={cam} isMain={false} reloadSignal={signals[cam.id] || 0} onSelect={() => selectMain(cam.id)} />
              </div>
            );
          })}
        </div>

        <button
          onClick={() => scroll('right')}
          className="absolute right-0 p-1.5 rounded-full bg-[#222] border border-[#333] text-[#aaa] hover:text-white hover:bg-[#333] transition-colors z-10"
          aria-label="Scroll Right"
        >
          <ChevronRight size={20} />
        </button>

      </div>

    </div>
  );
}
