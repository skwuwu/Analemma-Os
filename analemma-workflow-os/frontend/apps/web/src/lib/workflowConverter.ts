// 프론트엔드 워크플로우 데이터를 백엔드 형식으로 변환하는 유틸리티 함수들

// Subgraph 관련 타입
export interface SubgraphMetadata {
  name: string;
  description?: string;
  icon?: string;
  color?: string;
}

export interface SubgraphDefinition {
  id: string;
  nodes: any[];
  edges: any[];
  metadata: SubgraphMetadata;
  input_schema?: Record<string, SchemaField>;
  output_schema?: Record<string, SchemaField>;
}

export interface SchemaField {
  type: 'string' | 'number' | 'object' | 'array' | 'boolean' | 'any';
  description?: string;
  required?: boolean;
  default?: unknown;
}

export interface BackendWorkflow {
  name?: string;
  nodes: BackendNode[];
  edges: BackendEdge[];
  secrets?: BackendSecret[];
  // 서브그래프 지원
  subgraphs?: Record<string, SubgraphDefinition>;
}

export interface BackendNode {
  id: string;
  type: 'llm_chat' | 'operator' | 'api_call' | 'db_query' | 'for_each' | 'router' | 'trigger';
  provider?: 'openai' | 'bedrock';
  model?: string;
  prompt_content?: string;
  system_prompt?: string;
  temperature?: number;
  max_tokens?: number;
  writes_state_key?: string;
  sets?: { [key: string]: any };
  position?: { x: number; y: number };
  [key: string]: any;
}

export interface BackendEdge {
  type: 'normal' | 'edge' | 'flow' | 'if' | 'while' | 'hitp' | 'conditional_edge';
  source: string;
  target: string;
  condition?: string | { lhs: string; op: string; rhs: string };
  mapping?: { [key: string]: string };
  max_iterations?: number;
  [key: string]: any;
}

export interface BackendSecret {
  provider: 'secretsmanager' | 'ssm';
  name: string;
  target: string;
}

// 프론트엔드 노드를 백엔드 형식으로 변환
export const convertNodeToBackendFormat = (node: any): BackendNode => {
  const backendNode: BackendNode = {
    id: node.id,
    type: 'operator', // 기본값
  };

  switch (node.type) {
    case 'aiModel':
      backendNode.type = 'llm_chat';
      backendNode.provider = node.data.provider || 'openai';
      backendNode.model = node.data.model || 'gpt-4';
      backendNode.prompt_content = node.data.prompt_content || node.data.prompt || '';
      backendNode.system_prompt = node.data.system_prompt;
      backendNode.temperature = node.data.temperature || 0.7;
      backendNode.max_tokens = node.data.max_tokens || node.data.maxTokens || 256;
      backendNode.writes_state_key = node.data.writes_state_key;
      break;
    case 'operator':
      backendNode.type = 'operator';
      backendNode.sets = node.data.sets || {};
      break;
    case 'trigger':
      backendNode.type = 'operator';
      backendNode.sets = {
        _frontend_type: 'trigger',
        trigger_type: node.data.triggerType || 'request',
        triggerHour: node.data.triggerHour,
        triggerMinute: node.data.triggerMinute
      };
      break;
    case 'control':
      backendNode.type = 'operator';
      backendNode.sets = {
        _frontend_type: 'control',
        control_type: node.data.controlType || 'while',
        whileCondition: node.data.whileCondition,
        max_iterations: node.data.max_iterations || 10
      };
      break;
    default:
      backendNode.type = 'operator';
      backendNode.sets = {};
  }

  // Persist position if available so frontend layout is preserved
  if (node.position) {
    backendNode.position = node.position;
  }

  return backendNode;
};

// 프론트엔드 엣지를 백엔드 형식으로 변환
export const convertEdgeToBackendFormat = (edge: any, nodes: any[]): BackendEdge => {
  const sourceNode = nodes.find(n => n.id === edge.source);

  const backendEdge: BackendEdge = {
    type: 'edge', // 기본값을 edge로 설정
    source: edge.source,
    target: edge.target,
  };

  // 엣지 타입 매핑
  if (edge.type === 'default' || edge.type === 'smoothstep') {
    backendEdge.type = 'edge';
  } else if (edge.data?.condition) {
    backendEdge.type = 'if';
    backendEdge.condition = tryParseCondition(edge.data.condition) || edge.data.condition;
  }

  // Control 노드에서 나오는 엣지의 경우 while 타입만 설정 (조건은 노드에서 관리)
  if (sourceNode?.type === 'control' && sourceNode?.data?.controlType === 'while') {
    backendEdge.type = 'while';
  }

  return backendEdge;
};

// 조건 문자열을 구조화된 형태로 변환 시도
const tryParseCondition = (condition: string): { lhs: string; op: string; rhs: string } | null => {
  if (!condition || typeof condition !== 'string') {
    return null;
  }

  // 단순한 조건만 파싱 (복합 조건은 문자열 그대로 반환)
  const simplePattern = /^\s*([^<>=!]+?)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$/;
  const match = condition.trim().match(simplePattern);

  if (match) {
    const lhs = match[1].trim();
    const op = match[2].trim();
    let rhs = match[3].trim();

    // 따옴표로 감싸진 문자열인 경우에만 따옴표 제거
    if ((rhs.startsWith('"') && rhs.endsWith('"')) || (rhs.startsWith("'") && rhs.endsWith("'"))) {
      rhs = rhs.slice(1, -1);
    }

    return { lhs, op, rhs };
  }

  return null;
};

// 전체 워크플로우를 백엔드 형식으로 변환
export const convertWorkflowToBackendFormat = (workflow: any): BackendWorkflow => {
  const backendNodes = workflow.nodes?.map((node: any) => convertNodeToBackendFormat(node)) || [];
  const backendEdges = workflow.edges?.map((edge: any) => convertEdgeToBackendFormat(edge, workflow.nodes)) || [];

  // secrets 배열 변환 (프론트엔드에 secrets가 있는 경우)
  const backendSecrets = workflow.secrets?.map((secret: any) => ({
    provider: secret.provider || 'secretsmanager',
    name: secret.name,
    target: secret.target,
  })) || [];

  const result: BackendWorkflow = {
    name: workflow.name || 'untitled',
    nodes: backendNodes,
    edges: backendEdges,
  };

  // secrets가 있는 경우에만 추가
  if (backendSecrets.length > 0) {
    result.secrets = backendSecrets;
  }

  // subgraphs가 있는 경우 포함 (그룹 노드 지원)
  if (workflow.subgraphs && Object.keys(workflow.subgraphs).length > 0) {
    // 각 서브그래프의 노드/엣지도 백엔드 형식으로 변환
    const convertedSubgraphs: Record<string, SubgraphDefinition> = {};

    for (const [key, subgraph] of Object.entries(workflow.subgraphs as Record<string, any>)) {
      // _root는 내부 네비게이션용이므로 백엔드에 전송하지 않음
      if (key === '_root') continue;

      convertedSubgraphs[key] = {
        id: subgraph.id || key,
        nodes: subgraph.nodes?.map((node: any) => convertNodeToBackendFormat(node)) || [],
        edges: subgraph.edges?.map((edge: any) => convertEdgeToBackendFormat(edge, subgraph.nodes)) || [],
        metadata: subgraph.metadata || { name: key },
        input_schema: subgraph.input_schema,
        output_schema: subgraph.output_schema,
      };
    }

    if (Object.keys(convertedSubgraphs).length > 0) {
      result.subgraphs = convertedSubgraphs;
    }
  }

  return result;
};

// 백엔드 데이터를 프론트엔드 형식으로 변환 (로드 시 사용)
export const convertWorkflowFromBackendFormat = (backendWorkflow: any): any => {
  if (!backendWorkflow || (!backendWorkflow.nodes && !backendWorkflow.edges)) {
    return { nodes: [], edges: [] };
  }

  // Check if data is already in frontend format (has nodes with frontend types)
  const hasNodes = backendWorkflow.nodes && Array.isArray(backendWorkflow.nodes);
  if (hasNodes && backendWorkflow.nodes.length > 0) {
    const firstNode = backendWorkflow.nodes[0];
    // If the node has frontend-specific properties, assume it's already converted
    if (firstNode.type && ['aiModel', 'operator', 'trigger', 'control'].includes(firstNode.type) && firstNode.data) {
      console.log('Data appears to be in frontend format, returning as-is');
      return {
        name: backendWorkflow.name || 'Generated Workflow',
        nodes: backendWorkflow.nodes,
        edges: backendWorkflow.edges || [],
        secrets: backendWorkflow.secrets,
      };
    }
  }

  const frontendNodes = backendWorkflow.nodes?.map((node: BackendNode, index: number) => {
    // 백엔드 타입을 프론트엔드 타입으로 매핑
    let frontendType = 'operator';
    let label = 'Block';
    let nodeData: any = {};

    switch (node.type) {
      case 'llm_chat':
        frontendType = 'aiModel';
        label = 'AI Model';
        nodeData = {
          label,
          prompt_content: node.prompt_content,
          prompt: node.prompt_content,
          system_prompt: node.system_prompt,
          temperature: node.temperature,
          max_tokens: node.max_tokens,
          maxTokens: node.max_tokens,
          model: node.model,
          provider: node.provider,
          writes_state_key: node.writes_state_key,
        };
        break;
      case 'operator':
        // sets 내용을 확인하여 프론트엔드 타입 복원
        if (node.sets?._frontend_type === 'trigger') {
          frontendType = 'trigger';
          label = 'Trigger';
          nodeData = {
            label,
            triggerType: node.sets.trigger_type,
            triggerHour: node.sets.triggerHour,
            triggerMinute: node.sets.triggerMinute,
          };
        } else if (node.sets?._frontend_type === 'control') {
          frontendType = 'control';
          label = 'Control';
          nodeData = {
            label,
            controlType: node.sets.control_type,
            whileCondition: node.sets.whileCondition,
            max_iterations: node.sets.max_iterations,
          };
        } else {
          frontendType = 'operator';
          label = 'Operator';
          nodeData = {
            label,
            sets: node.sets,
          };
        }
        break;
      case 'trigger':
        // 백엔드에서 type이 trigger로 저장된 경우 (save_workflow.py의 정규화 로직)
        frontendType = 'trigger';
        label = 'Trigger';
        nodeData = {
          label,
          triggerType: node.sets?.trigger_type || 'request',
          triggerHour: node.sets?.triggerHour,
          triggerMinute: node.sets?.triggerMinute,
          // sets의 다른 속성들도 보존
          ...node.sets
        };
        break;
      default:
        frontendType = 'operator';
        label = 'Block';
        nodeData = { label };
    }

    return {
      id: node.id,
      type: frontendType,
      // Preserve stored position when available, otherwise fall back to grid placement
      position: node.position || { x: (index % 3) * 200 + 100, y: Math.floor(index / 3) * 150 + 100 },
      data: nodeData,
    };
  }) || [];

  const frontendEdges = backendWorkflow.edges?.map((edge: BackendEdge, index: number) => ({
    id: `edge-${index}`,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    animated: true,
    style: {
      stroke: edge.type === 'if' ? 'hsl(142 76% 36%)' : edge.type === 'while' ? 'hsl(38 92% 50%)' : 'hsl(263 70% 60%)',
      strokeWidth: 2
    },
    data: {
      condition: edge.condition,
      edgeType: edge.type, // 백엔드 엣지 타입을 프론트엔드에서 사용할 수 있도록 저장
    },
  })) || [];

  const result: any = {
    name: backendWorkflow.name,
    nodes: frontendNodes,
    edges: frontendEdges,
  };

  // secrets가 있는 경우 복원
  if (backendWorkflow.secrets && backendWorkflow.secrets.length > 0) {
    result.secrets = backendWorkflow.secrets;
  }

  return result;
};