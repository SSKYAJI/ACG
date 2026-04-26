/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "jsdom",
  transform: {
    "^.+\\.tsx?$": [
      "ts-jest",
      {
        tsconfig: {
          jsx: "react-jsx",
          module: "commonjs",
          moduleResolution: "node",
          esModuleInterop: true,
          allowJs: true,
          strict: true,
          noUncheckedIndexedAccess: true,
          resolveJsonModule: true,
          isolatedModules: true,
          baseUrl: ".",
          paths: { "~/*": ["./src/*"] },
        },
      },
    ],
  },
  moduleNameMapper: {
    "^~/(.*)$": "<rootDir>/src/$1",
  },
  transformIgnorePatterns: [
    "/node_modules/(?!(recharts|d3-.*|internmap|delaunator|robust-predicate)/)",
  ],
};
