import { useEffect } from 'react'

interface LightboxProps {
  src: string
  mimeType: string
  title?: string
  onClose: () => void
}

export default function Lightbox({ src, mimeType, title, onClose }: LightboxProps) {
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const isPdf = mimeType === 'application/pdf'
  const isText = mimeType === 'text/plain'

  return (
    <div
      className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-8"
      onClick={onClose}
    >
      <div
        className="relative max-w-[90vw] max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute -top-10 right-0 text-white/80 hover:text-white text-2xl z-10"
        >
          &times;
        </button>

        {title && (
          <div className="text-white/80 text-sm mb-2 truncate">{title}</div>
        )}

        {isPdf ? (
          <iframe
            src={src}
            className="w-[80vw] h-[80vh] rounded bg-white"
            title={title || 'Receipt PDF'}
          />
        ) : isText ? (
          <iframe
            src={src}
            className="w-[80vw] h-[80vh] rounded bg-white"
            title={title || 'Receipt text'}
          />
        ) : (
          <img
            src={src}
            alt={title || 'Receipt'}
            className="max-w-full max-h-[80vh] object-contain rounded"
          />
        )}
      </div>
    </div>
  )
}
