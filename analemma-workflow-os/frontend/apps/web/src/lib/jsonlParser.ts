/**
 * JSONL Parser: 견고한 스트리밍 JSON Lines 파서
 * 
 * LLM 스트리밍에서 발생할 수 있는 불완전한 JSON 조각을 안전하게 처리합니다.
 */

export interface ParsedChunk {
  type: 'complete' | 'partial' | 'error';
  data?: any;
  error?: string;
  raw?: string;
}

export class JSONLParser {
  private buffer: string = '';
  private partialBuffer: string = '';
  private lineNumber: number = 0;

  /**
   * 스트리밍 데이터 청크를 처리합니다.
   */
  processChunk(chunk: string): ParsedChunk[] {
    this.buffer += chunk;
    const results: ParsedChunk[] = [];

    // 완전한 라인들을 추출
    let lines: string[];
    if (this.buffer.includes('\n')) {
      const parts = this.buffer.split('\n');
      this.buffer = parts.pop() || ''; // 마지막 불완전한 라인은 버퍼에 보관
      lines = parts;
    } else {
      // 아직 완전한 라인이 없음
      return this.tryParsePartial();
    }

    // 각 라인 처리
    for (const line of lines) {
      this.lineNumber++;
      const trimmedLine = line.trim();
      
      if (!trimmedLine) {
        continue; // 빈 라인 무시
      }

      // SSE 형식 처리 (data: prefix)
      const jsonContent = trimmedLine.startsWith('data:') 
        ? trimmedLine.slice(5).trim() 
        : trimmedLine;

      if (!jsonContent) {
        continue;
      }

      const parseResult = this.parseJSONSafely(jsonContent);
      results.push(parseResult);
    }

    return results;
  }

  /**
   * 부분적인 JSON 파싱 시도
   */
  private tryParsePartial(): ParsedChunk[] {
    const currentContent = this.buffer.trim();
    
    if (!currentContent) {
      return [];
    }

    // 이전 부분 버퍼와 합쳐서 시도
    const combinedContent = this.partialBuffer + currentContent;
    
    // JSON 객체가 완성되었는지 확인
    if (this.isLikelyCompleteJSON(combinedContent)) {
      const parseResult = this.parseJSONSafely(combinedContent);
      
      if (parseResult.type === 'complete') {
        // 성공적으로 파싱됨
        this.partialBuffer = '';
        this.buffer = '';
        return [parseResult];
      }
    }

    // 부분 버퍼 업데이트
    this.partialBuffer = combinedContent;
    
    return [{
      type: 'partial',
      raw: currentContent
    }];
  }

  /**
   * JSON이 완성되었을 가능성이 있는지 휴리스틱 검사
   */
  private isLikelyCompleteJSON(content: string): boolean {
    const trimmed = content.trim();
    
    if (!trimmed) return false;
    
    // 기본적인 JSON 구조 검사
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      // 중괄호 균형 검사
      let braceCount = 0;
      let inString = false;
      let escaped = false;
      
      for (let i = 0; i < trimmed.length; i++) {
        const char = trimmed[i];
        
        if (escaped) {
          escaped = false;
          continue;
        }
        
        if (char === '\\') {
          escaped = true;
          continue;
        }
        
        if (char === '"') {
          inString = !inString;
          continue;
        }
        
        if (!inString) {
          if (char === '{') {
            braceCount++;
          } else if (char === '}') {
            braceCount--;
          }
        }
      }
      
      return braceCount === 0;
    }
    
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      // 배열 구조 검사 (간단한 버전)
      return true;
    }
    
    return false;
  }

  /**
   * 안전한 JSON 파싱
   */
  private parseJSONSafely(content: string): ParsedChunk {
    try {
      const parsed = JSON.parse(content);
      return {
        type: 'complete',
        data: parsed,
        raw: content
      };
    } catch (error) {
      // 일반적인 JSON 파싱 오류들을 분류
      const errorMessage = error instanceof Error ? error.message : String(error);
      
      if (errorMessage.includes('Unexpected end of JSON input')) {
        return {
          type: 'partial',
          error: 'incomplete_json',
          raw: content
        };
      }
      
      if (errorMessage.includes('Unexpected token')) {
        return {
          type: 'error',
          error: 'malformed_json',
          raw: content
        };
      }
      
      return {
        type: 'error',
        error: errorMessage,
        raw: content
      };
    }
  }

  /**
   * 파서 상태 초기화
   */
  reset(): void {
    this.buffer = '';
    this.partialBuffer = '';
    this.lineNumber = 0;
  }

  /**
   * 현재 파서 상태 정보
   */
  getStatus(): {
    bufferLength: number;
    partialBufferLength: number;
    lineNumber: number;
    hasPartialData: boolean;
  } {
    return {
      bufferLength: this.buffer.length,
      partialBufferLength: this.partialBuffer.length,
      lineNumber: this.lineNumber,
      hasPartialData: this.partialBuffer.length > 0
    };
  }

  /**
   * 남은 버퍼 내용을 강제로 처리 (스트림 종료 시)
   */
  flush(): ParsedChunk[] {
    const results: ParsedChunk[] = [];
    
    // 남은 버퍼 내용 처리
    if (this.buffer.trim()) {
      const parseResult = this.parseJSONSafely(this.buffer.trim());
      results.push(parseResult);
    }
    
    // 부분 버퍼 내용 처리
    if (this.partialBuffer.trim() && this.partialBuffer !== this.buffer) {
      const parseResult = this.parseJSONSafely(this.partialBuffer.trim());
      results.push(parseResult);
    }
    
    this.reset();
    return results;
  }
}

/**
 * 편의 함수: 단일 청크 파싱
 */
export function parseJSONLChunk(chunk: string): ParsedChunk[] {
  const parser = new JSONLParser();
  return parser.processChunk(chunk);
}

/**
 * 편의 함수: 완전한 JSONL 문자열 파싱
 */
export function parseJSONLString(jsonlString: string): ParsedChunk[] {
  const parser = new JSONLParser();
  const results = parser.processChunk(jsonlString);
  results.push(...parser.flush());
  return results;
}

export default JSONLParser;