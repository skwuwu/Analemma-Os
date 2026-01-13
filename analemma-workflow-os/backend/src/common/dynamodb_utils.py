# -*- coding: utf-8 -*-
"""
DynamoDB Utility Functions

공통 DynamoDB 리소스 관리를 위한 유틸리티입니다.
각 서비스에서 중복으로 정의되던 함수들을 통합합니다.
"""

import boto3
from functools import lru_cache


@lru_cache(maxsize=1)
def get_dynamodb_resource():
    """
    지연 초기화된 DynamoDB 리소스 반환
    
    싱글톤 패턴으로 리소스를 캐싱하여 재사용합니다.
    """
    return boto3.resource('dynamodb')


def get_dynamodb_table(table_name: str):
    """
    지정된 테이블 이름으로 DynamoDB Table 객체 반환
    
    Args:
        table_name: DynamoDB 테이블 이름
        
    Returns:
        boto3 DynamoDB Table 객체
    """
    return get_dynamodb_resource().Table(table_name)
