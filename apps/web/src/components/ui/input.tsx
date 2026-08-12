import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useId } from "react";

import styles from "./input.module.css";

interface FieldProps {
  label: string;
  error?: string | undefined;
  children: (id: string) => ReactNode;
}

function Field({ label, error, children }: FieldProps) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {error && <p className={styles.error}>{error}</p>}
    </div>
  );
}

interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  error?: string | undefined;
}

export function Input({ label, error, ...rest }: InputProps) {
  return (
    <Field label={label} error={error}>
      {(id) => <input id={id} className={styles.input} {...rest} />}
    </Field>
  );
}

interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> {
  label: string;
  options: readonly string[];
}

export function Select({ label, options, ...rest }: SelectProps) {
  return (
    <Field label={label}>
      {(id) => (
        <select id={id} className={styles.select} {...rest}>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
