USE [GapPricingPoC]
GO
/****** Object:  Table [dbo].[member_snapshot]    Script Date: 11/14/2026 10:12:51 AM ******/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
CREATE TABLE [dbo].[member_snapshot](
	[snapshot_id] [uniqueidentifier] NOT NULL,
	[member_id] [uniqueidentifier] NOT NULL,
	[run_id] [uniqueidentifier] NOT NULL,
	[raw_name] [nvarchar](200) NULL,
	[raw_dob] [date] NULL,
	[raw_salary] [float] NULL,
	[source_sheet] [nvarchar](50) NULL,
	[created_at] [datetime2](7) NOT NULL,
PRIMARY KEY CLUSTERED 
(
	[snapshot_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO
ALTER TABLE [dbo].[member_snapshot] ADD  DEFAULT (sysutcdatetime()) FOR [created_at]
GO
ALTER TABLE [dbo].[member_snapshot]  WITH CHECK ADD FOREIGN KEY([member_id])
REFERENCES [dbo].[member_identity] ([member_id])
GO
ALTER TABLE [dbo].[member_snapshot]  WITH CHECK ADD  CONSTRAINT [FK_member_snapshot_run] FOREIGN KEY([run_id])
REFERENCES [dbo].[run] ([run_id])
GO
ALTER TABLE [dbo].[member_snapshot] CHECK CONSTRAINT [FK_member_snapshot_run]
GO
