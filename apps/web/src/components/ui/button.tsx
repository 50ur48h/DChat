import type { ButtonHTMLAttributes } from "react";

import styles from "./button.module.css";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant | undefined;
}

/** One primary action per view; everything else is quieter. */
export function Button({ variant = "secondary", className, type, ...rest }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={`${styles.button} ${styles[variant]} ${className ?? ""}`}
      {...rest}
    />
  );
}
