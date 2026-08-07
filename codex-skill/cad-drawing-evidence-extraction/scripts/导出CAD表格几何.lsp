;; Read-only CAD entity export for table-boundary reconstruction.
;; It writes text plus LINE endpoints and LWPOLYLINE vertex bounds; it never
;; changes the opened drawing.
(vl-load-com)

(defun ct:clean (s)
  (if s (vl-string-translate "\t\n\r" "   " (vl-princ-to-string s)) "")
)

(defun ct:point-string (p)
  (if p
    (strcat (rtos (car p) 2 3) "," (rtos (cadr p) 2 3))
    ""
  )
)

(defun ct:mtext (e / item out)
  (setq out "")
  (foreach item e
    (if (member (car item) '(1 3)) (setq out (strcat out (cdr item))))
  )
  (ct:clean out)
)

(defun ct:lwpoly-bounds (e / item p xs ys)
  (setq xs '() ys '())
  (foreach item e
    (if (= (car item) 10)
      (progn
        (setq p (cdr item))
        (setq xs (cons (car p) xs))
        (setq ys (cons (cadr p) ys))
      )
    )
  )
  (if (and xs ys)
    (strcat "bounds=" (rtos (apply 'min xs) 2 3) "," (rtos (apply 'min ys) 2 3)
            "," (rtos (apply 'max xs) 2 3) "," (rtos (apply 'max ys) 2 3))
    ""
  )
)

(defun c:CTGX (/ file fh ss idx en e typ lay value extra p1 p2)
  (setq file (getenv "TABLE_GEOMETRY_OUTPUT"))
  (if (or (null file) (= file ""))
    (prompt "\nTABLE_GEOMETRY_OUTPUT is not set.")
    (progn
      (setq fh (open file "w"))
      (write-line "TYPE\tLAYER\tPOINT\tVALUE\tEXTRA" fh)
      (setq ss (ssget "_X"))
      (if ss
        (progn
          (setq idx 0)
          (while (< idx (sslength ss))
            (setq en (ssname ss idx) e (entget en) typ (cdr (assoc 0 e)) lay (cdr (assoc 8 e)) value "" extra "")
            (cond
              ((= typ "TEXT") (setq value (cdr (assoc 1 e))))
              ((= typ "MTEXT") (setq value (ct:mtext e)))
              ((= typ "LINE")
                (setq p1 (cdr (assoc 10 e)) p2 (cdr (assoc 11 e)))
                (setq extra (strcat "end=" (ct:point-string p2))))
              ((= typ "LWPOLYLINE") (setq extra (ct:lwpoly-bounds e)))
            )
            (write-line (strcat typ "\t" lay "\t" (ct:point-string (cdr (assoc 10 e))) "\t" (ct:clean value) "\t" (ct:clean extra)) fh)
            (setq idx (1+ idx))
          )
        )
      )
      (close fh)
      (prompt (strcat "\nCTGX exported: " file))
    )
  )
  (princ)
)
(princ "\nTable geometry exporter loaded. Run CTGX.")
