// The Service supplies this virtual module in production. Keep its data and
// routing hooks real; substitute only the Console host and visual primitives.
import React, { createContext, useContext } from "react";
import { Link, useSearchParams } from "react-router";
export {
  useMutation,
  useQuery,
  useInfiniteQuery,
  useQueryClient,
} from "@tanstack/react-query";
export { useTranslation } from "react-i18next";
export { React, Link, useSearchParams };

export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);
export const useAccess = () => ({ can: () => true });
export const useToast = () => ({ add() {} });
export const bridgeVersion = 1;

const Container = ({ children }) => <div>{children}</div>;
export const Sections = Container;
export const Toolbar = Container;
export const FormSection = Container;
export const Rail = Container;
export const RailSection = Container;
export const RailRow = ({ label, children }) => (
  <div>
    {label} {children}
  </div>
);
export const DetailLayout = ({ children, rail }) => (
  <div>
    {children}
    {rail}
  </div>
);
export const DetailPage = ({ header, children }) => (
  <main>
    {header}
    {children}
  </main>
);
export const DetailHeader = ({ name }) => <h1>{name}</h1>;
export const Page = ({ title, titleAction, actions, children }) => (
  <main>
    <h1>{title}</h1>
    {titleAction}
    {actions}
    {children}
  </main>
);
export const Section = ({ title, actions, children }) => (
  <section>
    <h2>{title}</h2>
    {actions}
    {children}
  </section>
);
export const Loading = () => <span>Loading</span>;
export const ErrorPage = ({ error }) => <p role="alert">{error.message}</p>;
export const ErrorNotice = ErrorPage;
export const Empty = ({ title, action }) => (
  <div>
    {title}
    {action}
  </div>
);
export const Badge = ({ children }) => <span>{children}</span>;
export const Timestamp = () => null;
export const Pagination = () => null;
export const ResourceTable = ({ columns, items, itemKey }) => (
  <table>
    <tbody>
      {items.map((row) => (
        <tr key={itemKey?.(row) ?? row.id}>
          {columns.map((column) => (
            <td key={column.label}>{column.render(row)}</td>
          ))}
        </tr>
      ))}
    </tbody>
  </table>
);
export const Button = ({ children, type = "button", onClick, disabled }) => (
  <button {...{ type, onClick, disabled }}>{children}</button>
);
export const Input = (props) => <input {...props} />;
export const Textarea = (props) => <textarea {...props} />;
export const FormField = ({ label, children }) => (
  <label>
    {label}
    {children}
  </label>
);
export const FormActions = ({ label = "Save", pending }) => (
  <button type="submit" disabled={pending}>
    {label}
  </button>
);
export const SaveBar = FormActions;
export const Confirm = () => null;
export const Switch = ({ checked, onCheckedChange }) => (
  <input
    type="checkbox"
    checked={checked}
    onChange={(e) => onCheckedChange(e.target.checked)}
  />
);
export const Checkbox = Switch;
export const Select = ({ children }) => <div>{children}</div>;
export const SelectTrigger = Container;
export const SelectValue = Container;
export const SelectPopup = () => null;
export const SelectItem = () => null;
export const ChoiceField = ({ label, value, options, onValueChange }) => (
  <select
    aria-label={label}
    value={value}
    onChange={(e) => onValueChange(e.target.value)}
  >
    {options?.map((item) => (
      <option key={item.value} value={item.value}>
        {item.label}
      </option>
    ))}
  </select>
);
export const SegmentedControl = ChoiceField;
export const SearchPicker = ChoiceField;
