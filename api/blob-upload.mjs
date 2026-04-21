import { handleUpload } from "@vercel/blob/client";

const ALLOWED_CONTENT_TYPES = [
  "text/csv",
  "application/csv",
  "application/vnd.ms-excel",
  "text/plain",
];

export async function POST(request) {
  try {
    const body = await request.json();
    const jsonResponse = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname) => {
        if (!pathname.startsWith("veriphone/uploads/")) {
          throw new Error("Invalid upload path.");
        }

        return {
          allowedContentTypes: ALLOWED_CONTENT_TYPES,
          addRandomSuffix: false,
          tokenPayload: JSON.stringify({ scope: "veriphone-upload" }),
        };
      },
      onUploadCompleted: async () => {
        return;
      },
    });

    return Response.json(jsonResponse);
  } catch (error) {
    return Response.json(
      {
        error: error instanceof Error ? error.message : "Could not initialize the upload.",
      },
      { status: 400 },
    );
  }
}
