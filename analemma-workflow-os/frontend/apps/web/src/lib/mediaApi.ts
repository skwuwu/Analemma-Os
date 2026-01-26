/**
 * Media Upload API Client
 * ========================
 * 프론트엔드에서 미디어 파일을 S3에 업로드하는 API 클라이언트
 */

import { makeAuthenticatedRequest, parseApiResponse } from './api';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

// 지원되는 MIME 타입
export const SUPPORTED_MEDIA_TYPES = [
  'image/jpeg',
  'image/png', 
  'image/gif',
  'image/webp',
  'video/mp4',
  'video/webm',
  'application/pdf',
  'audio/mpeg',
  'audio/wav',
] as const;

export type SupportedMediaType = typeof SUPPORTED_MEDIA_TYPES[number];

// 최대 파일 크기 (MB)
export const MAX_FILE_SIZE_MB = 50;
export const MAX_DIRECT_UPLOAD_SIZE_MB = 10; // 직접 업로드 최대 크기

export interface MediaUploadResponse {
  success: boolean;
  s3_url: string;
  https_url: string;
  s3_key: string;
  content_type: string;
  size_bytes: number;
  sha256_hash: string;
}

export interface PresignResponse {
  success: boolean;
  upload_url: string;
  s3_url: string;
  s3_key: string;
  expires_in: number;
}

export interface UploadedMedia {
  filename: string;
  s3_url: string;
  https_url: string;
  content_type: string;
  size_bytes: number;
}

/**
 * 파일을 Base64로 변환
 */
export const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => {
      const result = reader.result as string;
      // data:image/png;base64,... 형식 그대로 반환 (서버가 처리)
      resolve(result);
    };
    reader.onerror = (error) => reject(error);
  });
};

/**
 * MIME 타입이 지원되는지 확인
 */
export const isSupportedMediaType = (mimeType: string): boolean => {
  return SUPPORTED_MEDIA_TYPES.includes(mimeType as SupportedMediaType);
};

/**
 * 파일 크기 검증
 */
export const validateFileSize = (file: File): { valid: boolean; error?: string } => {
  const sizeMB = file.size / (1024 * 1024);
  
  if (sizeMB > MAX_FILE_SIZE_MB) {
    return {
      valid: false,
      error: `파일 크기가 ${MAX_FILE_SIZE_MB}MB를 초과합니다. (현재: ${sizeMB.toFixed(1)}MB)`,
    };
  }
  
  return { valid: true };
};

/**
 * 직접 업로드 (작은 파일용, < 10MB)
 */
export const uploadMediaDirect = async (file: File): Promise<MediaUploadResponse> => {
  const sizeValidation = validateFileSize(file);
  if (!sizeValidation.valid) {
    throw new Error(sizeValidation.error);
  }

  if (!isSupportedMediaType(file.type)) {
    throw new Error(`지원되지 않는 파일 형식입니다: ${file.type}`);
  }

  const base64Data = await fileToBase64(file);
  
  const response = await makeAuthenticatedRequest(`${API_BASE}/api/media/upload`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type,
      data: base64Data,
    }),
  });

  return await parseApiResponse(response);
};

/**
 * Presigned URL 요청 후 직접 S3에 업로드 (대용량 파일용)
 */
export const uploadMediaPresigned = async (
  file: File,
  onProgress?: (percent: number) => void
): Promise<UploadedMedia> => {
  const sizeValidation = validateFileSize(file);
  if (!sizeValidation.valid) {
    throw new Error(sizeValidation.error);
  }

  if (!isSupportedMediaType(file.type)) {
    throw new Error(`지원되지 않는 파일 형식입니다: ${file.type}`);
  }

  // 1. Presigned URL 요청
  const presignResponse = await makeAuthenticatedRequest(`${API_BASE}/api/media/presign`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type,
    }),
  });

  const presignData: PresignResponse = await parseApiResponse(presignResponse);

  // 2. S3에 직접 PUT (XHR 사용하여 progress 지원)
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && onProgress) {
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress(percent);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`S3 upload failed: ${xhr.status} ${xhr.statusText}`));
      }
    });

    xhr.addEventListener('error', () => {
      reject(new Error('Network error during S3 upload'));
    });

    xhr.open('PUT', presignData.upload_url);
    xhr.setRequestHeader('Content-Type', file.type);
    xhr.send(file);
  });

  return {
    filename: file.name,
    s3_url: presignData.s3_url,
    https_url: presignData.s3_url.replace('s3://', 'https://s3.amazonaws.com/'),
    content_type: file.type,
    size_bytes: file.size,
  };
};

/**
 * 자동 업로드 (파일 크기에 따라 적절한 방법 선택)
 */
export const uploadMedia = async (
  file: File,
  onProgress?: (percent: number) => void
): Promise<UploadedMedia> => {
  const sizeMB = file.size / (1024 * 1024);

  if (sizeMB <= MAX_DIRECT_UPLOAD_SIZE_MB) {
    // 작은 파일: 직접 업로드
    const result = await uploadMediaDirect(file);
    onProgress?.(100);
    return {
      filename: file.name,
      s3_url: result.s3_url,
      https_url: result.https_url,
      content_type: result.content_type,
      size_bytes: result.size_bytes,
    };
  } else {
    // 큰 파일: Presigned URL 사용
    return await uploadMediaPresigned(file, onProgress);
  }
};

/**
 * 여러 파일 업로드
 */
export const uploadMultipleMedia = async (
  files: File[],
  onProgress?: (fileIndex: number, percent: number) => void
): Promise<UploadedMedia[]> => {
  const results: UploadedMedia[] = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const result = await uploadMedia(file, (percent) => {
      onProgress?.(i, percent);
    });
    results.push(result);
  }

  return results;
};
