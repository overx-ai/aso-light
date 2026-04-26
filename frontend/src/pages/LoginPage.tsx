import { Link, useNavigate } from "react-router-dom";
import {
  TextInput,
  PasswordInput,
  Button,
  Paper,
  Title,
  Text,
  Container,
  Anchor,
  Stack,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useAuth } from "@/lib/auth";
import type { LoginRequest } from "@/types";

export default function LoginPage() {
  const navigate = useNavigate();
  const { login } = useAuth();

  const form = useForm<LoginRequest>({
    initialValues: {
      email: "",
      password: "",
    },
    validate: {
      email: (value) => (/^\S+@\S+$/.test(value) ? null : "Invalid email"),
      password: (value) =>
        value.length >= 1 ? null : "Password is required",
    },
  });

  const handleSubmit = async (values: LoginRequest) => {
    try {
      await login(values);
      navigate("/");
    } catch {
      notifications.show({
        title: "Login failed",
        message: "Invalid email or password",
        color: "red",
      });
    }
  };

  return (
    <Container size={420} py={80}>
      <Title ta="center" fw={700}>
        Welcome back
      </Title>
      <Text c="dimmed" size="sm" ta="center" mt={5}>
        Don&apos;t have an account?{" "}
        <Anchor component={Link} to="/register" size="sm">
          Register
        </Anchor>
      </Text>

      <Paper withBorder shadow="md" p={30} mt={30} radius="md">
        <form onSubmit={form.onSubmit(handleSubmit)}>
          <Stack>
            <TextInput
              label="Email"
              placeholder="you@example.com"
              {...form.getInputProps("email")}
            />
            <PasswordInput
              label="Password"
              placeholder="Your password"
              {...form.getInputProps("password")}
            />
            <Button type="submit" fullWidth mt="md">
              Sign in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Container>
  );
}
