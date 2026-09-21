import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, MessagePlugin, Tabs, Form } from 'tdesign-react'
import { login, register } from '../api/auth'

const { TabPanel } = Tabs
const { FormItem } = Form

export default function Login() {
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleLogin = async () => {
    if (!phone || !password) {
      MessagePlugin.warning('请填写手机号和密码')
      return
    }
    setLoading(true)
    try {
      const res = await login(phone, password)
      localStorage.setItem('token', res.data.token)
      localStorage.setItem('user', JSON.stringify(res.data))
      MessagePlugin.success('登录成功')
      window.location.href = '/chat'
    } catch (err) {
      MessagePlugin.error(err.response?.data?.detail || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  const handleRegister = async () => {
    if (!phone || !password) {
      MessagePlugin.warning('请填写手机号和密码')
      return
    }
    setLoading(true)
    try {
      const res = await register(phone, password)
      MessagePlugin.success(`注册成功，欢迎 ${res.data.phone}`)
      // 注册后自动登录
      await handleLogin()
    } catch (err) {
      MessagePlugin.error(err.response?.data?.detail || '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      maxWidth: 400, margin: '100px auto', padding: 24,
      background: '#fff', borderRadius: 8, boxShadow: '0 2px 12px rgba(0,0,0,0.08)',
    }}>
      <h2 style={{ textAlign: 'center', marginBottom: 24 }}>AI 人际关系助手</h2>
      <Tabs defaultValue="login">
        <TabPanel value="login" label="登录">
          <Form labelAlign="top">
            <FormItem label="手机号">
              <Input value={phone} onChange={setPhone} placeholder="请输入手机号" maxlength={11} />
            </FormItem>
            <FormItem label="密码">
              <Input type="password" value={password} onChange={setPassword} placeholder="请输入密码" />
            </FormItem>
            <FormItem>
              <Button theme="primary" block loading={loading} onClick={handleLogin}>
                登录
              </Button>
            </FormItem>
          </Form>
        </TabPanel>
        <TabPanel value="register" label="注册">
          <Form labelAlign="top">
            <FormItem label="手机号">
              <Input value={phone} onChange={setPhone} placeholder="请输入手机号" maxlength={11} />
            </FormItem>
            <FormItem label="密码">
              <Input type="password" value={password} onChange={setPassword} placeholder="请设置密码（至少6位）" />
            </FormItem>
            <FormItem>
              <Button theme="primary" block loading={loading} onClick={handleRegister}>
                注册
              </Button>
            </FormItem>
          </Form>
        </TabPanel>
      </Tabs>
    </div>
  )
}