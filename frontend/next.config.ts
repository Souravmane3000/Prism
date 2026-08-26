import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow react-markdown's remark/rehype packages (ESM)
  transpilePackages: ["react-markdown", "remark-parse", "remark-rehype"],
};

export default nextConfig;
