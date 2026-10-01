/*
 Navicat Premium Data Transfer

 Source Server         : 林染染阿里云MySQL8
 Source Server Type    : MySQL
 Source Server Version : 80034
 Source Host           : 47.109.139.48:3306
 Source Schema         : relation_agent

 Target Server Type    : MySQL
 Target Server Version : 80034
 File Encoding         : 65001

 Date: 25/09/2026 12:18:31
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for agent_request
-- ----------------------------
DROP TABLE IF EXISTS `agent_request`;
CREATE TABLE `agent_request`  (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `owner_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `thread_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `message_hash` char(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `response` json NULL,
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL,
  `execution_token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL,
  `create_time` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `update_time` datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_agent_request_owner_request`(`owner_id`, `request_id`) USING BTREE,
  INDEX `idx_agent_request_thread`(`owner_id`, `thread_id`, `create_time`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 1 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Records of agent_request
-- ----------------------------

-- ----------------------------
-- Table structure for tool_execution
-- ----------------------------
DROP TABLE IF EXISTS `tool_execution`;
CREATE TABLE `tool_execution`  (
  `id` bigint UNSIGNED NOT NULL AUTO_INCREMENT,
  `thread_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '会话线程ID',
  `tool_name` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '工具名',
  `call_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '工具调用id',
  `owner_id` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL COMMENT '调用用户id',
  `request_id` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `arguments` json NULL COMMENT 'LLM 返回的原始参数',
  `result` json NULL COMMENT '执行结果',
  `status` enum('success','failed','running') CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL DEFAULT 'running',
  `error_message` text CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NULL COMMENT '失败原因',
  `latency_ms` int UNSIGNED NULL DEFAULT NULL COMMENT '执行耗时(毫秒)',
  `execution_token` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  `create_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  `update_time` datetime(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (`id`) USING BTREE,
  UNIQUE INDEX `uq_tool_execution_owner_call`(`owner_id`, `call_id`) USING BTREE,
  INDEX `idx_thread_created`(`thread_id`, `create_time`) USING BTREE,
  INDEX `idx_call_id_owner_id`(`call_id`, `owner_id`) USING BTREE,
  INDEX `idx_tool_status`(`tool_name`, `status`) USING BTREE,
  INDEX `idx_tool_execution_request`(`owner_id`, `request_id`, `create_time`) USING BTREE
) ENGINE = InnoDB AUTO_INCREMENT = 2 CHARACTER SET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci ROW_FORMAT = Dynamic;

-- ----------------------------
-- Records of tool_execution
-- ----------------------------

SET FOREIGN_KEY_CHECKS = 1;
