import React, { useEffect, useRef } from "react";

export interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  kind?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmModal({
  isOpen,
  title,
  message,
  confirmText = "Xác nhận",
  cancelText = "Hủy bỏ",
  kind = "primary",
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
      } else if (event.key === "Enter") {
        onConfirm();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    const timer = setTimeout(() => confirmBtnRef.current?.focus(), 50);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      clearTimeout(timer);
    };
  }, [isOpen, onCancel, onConfirm]);

  if (!isOpen) return null;

  return (
    <div
      className="apple-modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
    >
      <div className="apple-modal-dialog">
        <div className="apple-modal-header">
          <div className={`apple-modal-icon ${kind}`}>
            {kind === "danger" ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <line x1="12" y1="8" x2="12" y2="12"></line>
                <line x1="12" y1="16" x2="12.01" y2="16"></line>
              </svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
              </svg>
            )}
          </div>
          <h3 id="confirm-modal-title">{title}</h3>
        </div>

        <div className="apple-modal-body">
          <p>{message}</p>
        </div>

        <div className="apple-modal-footer">
          <button
            type="button"
            className="apple-modal-btn apple-modal-btn-cancel"
            onClick={onCancel}
          >
            {cancelText}
          </button>
          <button
            ref={confirmBtnRef}
            type="button"
            className={`apple-modal-btn apple-modal-btn-${kind}`}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
