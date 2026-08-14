import { Search } from "lucide-react";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}

/** Text field with a leading magnifier, used for the client-side list filters. */
export function SearchInput({ value, onChange, placeholder }: SearchInputProps) {
  return (
    <InputGroup>
      <InputGroupAddon>
        <Search />
      </InputGroupAddon>
      <InputGroupInput
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </InputGroup>
  );
}
