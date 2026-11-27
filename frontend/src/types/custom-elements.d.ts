import type * as React from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      selectedcontent: React.HTMLAttributes<HTMLElement>;
    }
  }
}
